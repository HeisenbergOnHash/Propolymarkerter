# FULL LOGIC - REASONING
from pydantic import BaseModel

class Input(BaseModel): market: dict
class Output(BaseModel): proposal: dict

def run(data: Input) -> Output:
    return Output(proposal={"probability":0.6,"edge":0.1,"action":"BUY"})

