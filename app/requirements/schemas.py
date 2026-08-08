from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from app.common.enums import ListingType, PreferenceImportance

class PreferenceValue(BaseModel):
    value: str | int | bool
    importance: PreferenceImportance

class RequirementExtractionResponse(BaseModel):
    listing_types: List[str] = Field(description="e.g. ENTIRE_PROPERTY, PRIVATE_ROOM, SHARED_ROOM")
    preferred_locations: List[str] = Field(description="List of areas/localities desired")
    acceptable_locations: List[str] = Field(default=[], description="List of secondary or acceptable areas")
    excluded_locations: List[str] = Field(default=[], description="List of explicitly excluded areas")
    work_location: Optional[str] = Field(default=None, description="Where the renter works, if mentioned")
    target_rent: int = Field(description="The desired or typical rent amount they want to pay")
    max_rent: int = Field(description="The absolute maximum rent they can afford")
    preferred_move_in_date: Optional[str] = Field(default=None, description="ISO format YYYY-MM-DD")
    latest_move_in_date: Optional[str] = Field(default=None, description="ISO format YYYY-MM-DD")
    preferred_property_configurations: Optional[List[str]] = Field(default=None, description="e.g. 1BHK, 2BHK, 3BHK")
    core_preferences: Dict[str, PreferenceValue] = Field(default={}, description="Dict of explicit preferences like furnishing, attached_bathroom, brokerage")
    additional_preferences: Dict[str, str] = Field(default={}, description="Any other free-form preferences not covered above")
