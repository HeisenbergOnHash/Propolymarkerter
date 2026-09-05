# FULL LOGIC - OPPORTUNITY_FINDER
from pydantic import BaseModel

class Input(BaseModel): markets: list
class Output(BaseModel): ranked: list

def run(data: Input) -> Output:
    ranked = sorted(data.markets, key=lambda x: x.get("volume",0), reverse=True)
    return Output(ranked=ranked[:5])

