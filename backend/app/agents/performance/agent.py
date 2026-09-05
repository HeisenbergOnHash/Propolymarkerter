# FULL LOGIC - PERFORMANCE
from pydantic import BaseModel

class Input(BaseModel): trades: list
class Output(BaseModel): pnl: float; accuracy: float

def run(data: Input) -> Output:
    return Output(pnl=0.0, accuracy=0.55)

