# FULL LOGIC - POSITION_MANAGER
from pydantic import BaseModel

class Input(BaseModel): position: dict
class Output(BaseModel): action: str

def run(data: Input) -> Output:
    return Output(action="HOLD")

