from app.models.risk_zone import RiskZone
from .base import BaseRepository

class RiskZoneRepository(BaseRepository[RiskZone]):
    pass

risk_zone_repo = RiskZoneRepository(RiskZone)
