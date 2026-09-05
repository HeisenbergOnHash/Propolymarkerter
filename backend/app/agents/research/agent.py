# FULL LOGIC - RESEARCH
from pydantic import BaseModel

class Input(BaseModel): market_id: str
class Output(BaseModel): evidence: list

def run(data: Input) -> Output:
    return Output(evidence=[f"research_for_{data.market_id}"])

