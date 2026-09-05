# FULL LOGIC - RISK_MANAGER
from pydantic import BaseModel

class Input(BaseModel): proposal: dict
class Output(BaseModel): allowed: bool; size: int

def run(data: Input) -> Output:
    return Output(allowed=True, size=min(proposal.get("size",10),50))

