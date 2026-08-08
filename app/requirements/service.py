import json
from uuid import UUID
from datetime import datetime, date
from app.llm.gemini import GeminiProvider
from app.requirements.schemas import RequirementExtractionResponse
from app.db.models import SearchSession, SearchRequirement
from app.common.enums import SearchStatus, ListingType
from app.db.client import get_supabase_client

class RequirementService:
    def __init__(self):
        self.llm = GeminiProvider()
        self.db = get_supabase_client()

    async def parse_requirements(self, text: str) -> RequirementExtractionResponse:
        prompt = f"""
        Extract the rental requirements from the following user input:
        "{text}"
        
        Respond with a JSON matching the RequirementExtractionResponse schema.
        Ensure list values are proper lists. For listing_types use ENTIRE_PROPERTY, PRIVATE_ROOM, SHARED_ROOM.
        If any value is missing, try to infer reasonably or leave it null/empty.
        """
        # Use structured parsing directly
        try:
            return await self.llm.generate_structured(prompt, RequirementExtractionResponse)
        except Exception as e:
            raise ValueError(f"Failed to parse requirements: {e}")

    def create_search(self, user_id: UUID, requirement_data: RequirementExtractionResponse, raw_text: str) -> SearchSession:
        # Create Search Session
        session_res = self.db.table("search_sessions").insert({
            "user_id": str(user_id),
            "status": SearchStatus.ACTIVE.value,
            "city": "Hyderabad"
        }).execute()
        
        if not session_res.data:
            raise Exception("Failed to create search session")
        
        session = SearchSession(**session_res.data[0])
        
        # Insert requirement
        req_res = self.db.table("search_requirements").insert({
            "search_id": str(session.id),
            "listing_types": requirement_data.listing_types,
            "preferred_locations": requirement_data.preferred_locations,
            "target_rent": requirement_data.target_rent,
            "max_rent": requirement_data.max_rent,
            "raw_requirement_text": raw_text,
            "core_preferences": {k: v.dict() for k,v in requirement_data.core_preferences.items()}
        }).execute()

        return session
