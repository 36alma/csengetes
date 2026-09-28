from typing import Optional
from pydantic import BaseModel
from datetime import datetime
class TimeModel(BaseModel):
    datetime:Optional[datetime] = None
    hours:int
    minute:int
