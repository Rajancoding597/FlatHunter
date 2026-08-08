from pydantic import BaseModel, Field
from typing import List

class AvailabilityWindow(BaseModel):
    day_type: str = Field(description="'WEEKDAY', 'WEEKEND', or 'SPECIFIC_DATE'")
    start_time: str = Field(description="Format HH:MM in 24h")
    end_time: str = Field(description="Format HH:MM in 24h")

class RenterAvailabilityExtraction(BaseModel):
    general_windows: List[AvailabilityWindow] = Field(description="The general weekly availability windows")
