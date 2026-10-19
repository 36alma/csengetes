from pydantic import BaseModel
class InputDevice(BaseModel):
    index:int
    name:str