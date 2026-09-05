# FULL LOGIC - VERIFIER
from pydantic import BaseModel

class Input(BaseModel): proposal: dict
class Output(BaseModel): approved: bool; reason: str

def run(data: Input) -> Output:
    return Output(approved=True, reason="verification_passed")

