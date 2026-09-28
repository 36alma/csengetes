from pydantic import BaseModel

class Music(BaseModel):
    youtube_url: str
    name:str
    preferredquality: int
