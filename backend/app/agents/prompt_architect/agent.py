# FULL LOGIC - PROMPT_ARCHITECT
from pydantic import BaseModel

class Input(BaseModel): context: dict
class Output(BaseModel): prompt: str

def run(data: Input) -> Output:
    return Output(prompt=f"Task: {context.get('task')}. Rules: stable.")

