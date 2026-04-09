from app.models.field import Field
from .base import BaseRepository

class FieldRepository(BaseRepository[Field]):
    pass

field_repo = FieldRepository(Field)
