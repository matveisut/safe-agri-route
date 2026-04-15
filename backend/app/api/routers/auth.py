"""
Authentication endpoints.

POST /auth/register — create a new user account
POST /auth/login    — issue a JWT access token
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models.user import User
from app.repositories.user import user_repo

router = APIRouter(prefix="/auth", tags=["Auth"])


# ---------------------------------------------------------------------------
# Schemas (local — no need to pollute schemas/mission.py)
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: str = "viewer"   # "operator" | "viewer"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new user account.

    Intended for first-run / admin bootstrap.  In production this endpoint
    should be disabled or protected by an admin token.
    """
    existing = await user_repo.get_by_email(db, body.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    await user_repo.create(db, obj_in={
        "email": body.email,
        "hashed_password": hash_password(body.password),
        "role": body.role,
        "is_active": True,
    })
    return {"message": "User created", "email": body.email, "role": body.role}


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with email + password and receive a Bearer token.

    Uses OAuth2 password-flow form fields:
        username = email address
        password = plain-text password
    """
    user = await user_repo.get_by_email(db, form.username)
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account disabled",
        )

    token = create_access_token({"sub": user.email, "role": user.role})
    return TokenResponse(access_token=token)
