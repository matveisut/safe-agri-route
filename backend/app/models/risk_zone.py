from sqlalchemy import Column, Integer, String, Float
from geoalchemy2 import Geometry
from .base import Base

class RiskZone(Base):
    __tablename__ = "risk_zones"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, index=True, nullable=False) # jamming, spoofing, etc
    # Using SRID 4326 as specified in the schema requirements
    geometry = Column(Geometry(geometry_type='POLYGON', srid=4326), nullable=False)
    severity_weight = Column(Float, nullable=False, default=1.0)
