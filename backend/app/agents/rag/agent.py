# FULL LOGIC - RAG
from pydantic import BaseModel

class Input(BaseModel): query: str
class Output(BaseModel): similar: list

def run(data: Input) -> Output:
    return Output(similar=[f"similar_to_{data.query}"])

