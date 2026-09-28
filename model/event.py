
from pydantic import BaseModel
from model.time import TimeModel
class Event(BaseModel):
    file:str
    time:TimeModel