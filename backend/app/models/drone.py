from sqlalchemy import Column, Integer, String, Float
from .base import Base

class Drone(Base):
    __tablename__ = "drones"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    battery_capacity = Column(Integer, nullable=False) # in mAh or percentages
    max_speed = Column(Float, nullable=False)          # in m/s
    status = Column(String, default="idle")              # idle, in_flight, charging, error
