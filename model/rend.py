from pydantic import BaseModel
from . import Event
class Rend(BaseModel):
    events: list[Event]
    name:str