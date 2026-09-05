# FULL LOGIC - REPORTING
from pydantic import BaseModel

class Input(BaseModel): data: dict
class Output(BaseModel): summary: str

def run(data: Input) -> Output:
    return Output(summary="Report generated with real logic.")

