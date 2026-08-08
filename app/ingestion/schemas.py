from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from app.common.enums import ListingType

class PropertyContact(BaseModel):
    name: Optional[str] = None
    role: str = Field(description="OWNER, BROKER, CURRENT_TENANT, or UNKNOWN")
    phones: List[str] = []

class ListingExtractionResponse(BaseModel):
    listing_type: str = Field(description="ENTIRE_PROPERTY, PRIVATE_ROOM, SHARED_ROOM")
    city: str
    locality: str
    rent: int
    deposit: Optional[int] = None
    maintenance: Optional[int] = None
    brokerage: Optional[int] = None
    available_from: Optional[str] = None
    furnishing: Optional[str] = None
    attached_bathroom: Optional[bool] = None
    car_parking: Optional[bool] = None
    contacts: List[PropertyContact] = []
    extracted_context: Dict[str, Any] = Field(default={}, description="Other useful flexible details")
