from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from app.common.enums import ListingType, PreferenceImportance

class PreferenceValue(BaseModel):
    value: str | int | bool
    importance: PreferenceImportance

class RequirementExtractionResponse(BaseModel):
    is_complete: bool = Field(description="True ONLY if you have gathered the user's Location, Budget, and Property Type (BHK). False if any of these are missing.")
    follow_up_question: Optional[str] = Field(default=None, description="If is_complete is False, generate a natural, conversational question to ask the user for the missing mandatory info.")
    conversational_summary: Optional[str] = Field(default=None, description="If is_complete is True, write a friendly confirmation message to the user summarizing their search.")
    
    listing_types: List[str] = Field(default=[], description="e.g. ENTIRE_PROPERTY, PRIVATE_ROOM, SHARED_ROOM. Empty if not mentioned.")
    preferred_locations: List[str] = Field(default=[], description="List of areas/localities desired. Empty if not mentioned.")
    acceptable_locations: List[str] = Field(default=[], description="List of secondary or acceptable areas")
    excluded_locations: List[str] = Field(default=[], description="List of explicitly excluded areas")
    work_location: Optional[str] = Field(default=None, description="Where the renter works, if mentioned")
    target_rent: Optional[int] = Field(default=None, description="The desired or typical rent amount they want to pay. Null if not mentioned.")
    max_rent: Optional[int] = Field(default=None, description="The absolute maximum rent they can afford. Null if not mentioned.")
    preferred_move_in_date: Optional[str] = Field(default=None, description="ISO format YYYY-MM-DD")
    latest_move_in_date: Optional[str] = Field(default=None, description="ISO format YYYY-MM-DD")
    preferred_property_configurations: Optional[List[str]] = Field(default=None, description="e.g. 1BHK, 2BHK, 3BHK")
    core_preferences: Dict[str, PreferenceValue] = Field(default={}, description="Dict of explicit preferences like furnishing, attached_bathroom, brokerage")
    additional_preferences: Dict[str, str] = Field(default={}, description="Any other free-form preferences not covered above")
