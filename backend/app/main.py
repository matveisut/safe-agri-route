import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env before any app imports so JWT_SECRET etc. are available
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.api.routers import mission, telemetry
from app.api.routers.auth import router as auth_router
from app.services.mavlink_service import mavlink_service

# Make app-level loggers visible alongside uvicorn output
logging.getLogger("app").setLevel(logging.DEBUG)
logging.getLogger("app").addHandler(logging.StreamHandler())


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup: attempt MAVLink connections (non-fatal if SITL unreachable)
    await mavlink_service.connect_all()
    yield
    # Shutdown: nothing to clean up for now


app = FastAPI(
    title="SafeAgriRoute MVP API",
    description="Backend allowing routing of agricultural drones across risk constraints.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration to allow frontend to connect easily
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For dev, usually specific ports like localhost:5173
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth_router)                    # /auth/login, /auth/register
app.include_router(mission.router, prefix="/api/v1")
app.include_router(telemetry.router)               # WebSocket /ws/...


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "SafeAgriRoute Backend is running"}
