from sqlalchemy import Column, Integer, String
from geoalchemy2 import Geometry
from .base import Base

class Field(Base):
    __tablename__ = "fields"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    # Using SRID 4326 as specified in the schema requirements
    geometry = Column(Geometry(geometry_type='POLYGON', srid=4326), nullable=False)
