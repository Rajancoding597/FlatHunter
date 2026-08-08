from pydantic import BaseModel, Field
from typing import Optional

class ExtractedFacts(BaseModel):
    rent: Optional[int] = Field(default=None, description="Updated rent if mentioned")
    deposit: Optional[int] = Field(default=None, description="Security deposit amount if mentioned")
    brokerage: Optional[int] = Field(default=None, description="Brokerage amount if mentioned")
    maintenance: Optional[int] = Field(default=None, description="Maintenance amount if mentioned")
    car_parking: Optional[bool] = Field(default=None, description="Is car parking explicitly mentioned as included?")

class ReplyClassification(BaseModel):
    availability: str = Field(description="Must be 'AVAILABLE', 'UNAVAILABLE', or 'UNCLEAR'")
    facts: ExtractedFacts = Field(description="Any concrete facts extracted from the reply")
    next_question: Optional[str] = Field(default=None, description="If available but missing crucial info (like deposit), draft a polite follow-up question. Max 1 sentence.")
    proposed_visit_time: Optional[str] = Field(default=None, description="If the owner proposes a time to visit, extract it in ISO 8601 format if possible.")
