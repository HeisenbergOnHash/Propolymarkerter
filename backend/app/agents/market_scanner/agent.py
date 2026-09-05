# FULL LOGIC - MARKET_SCANNER
from pydantic import BaseModel

from pydantic import BaseModel
class Input(BaseModel): market_list: list
class Output(BaseModel): filtered: list

def run(data: Input) -> Output:
    # Real deterministic filter logic
    filtered = [m for m in data.market_list if m.get("status")=="open"]
    return Output(filtered=filtered)

