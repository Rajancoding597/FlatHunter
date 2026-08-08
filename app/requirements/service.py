import json
from uuid import UUID
from datetime import datetime, date
from app.llm.gemini import get_llm_provider
from app.requirements.schemas import RequirementExtractionResponse
from app.db.models import SearchSession, SearchRequirement
from app.common.enums import SearchStatus, ListingType
from app.db.client import get_supabase_client

class RequirementService:
    def __init__(self):
        self.llm = get_llm_provider()
        self.db = get_supabase_client()

    async def parse_requirements(self, text: str) -> RequirementExtractionResponse:
        prompt = f"""
        You are a friendly rental search assistant having a conversation with a renter.
        Analyze the conversation below and extract rental requirements.
        
        Conversation:
        "{text}"
        
        RULES:
        - Location, Budget (target_rent or max_rent), and Property Type (e.g. 1BHK, 2BHK, Private Room) are MANDATORY.
        - If ANY mandatory field is still missing from the conversation, set is_complete to False.
        - When is_complete is False, write a follow_up_question that:
          * Acknowledges what the user just said (e.g. "Manikonda is a great area!")
          * Asks for ONLY the single most important missing piece naturally
          * Sounds like a friendly human, not a form
          * Is 1-2 sentences max
        - When is_complete is True, write a conversational_summary that:
          * Summarizes their requirements in plain, friendly language
          * Uses emojis sparingly for warmth
          * Does NOT mention internal terms like "search session" or "listing type"
          * Example: "Got it! I'll look for a 2BHK in Manikonda within a budget of ₹40,000."
        - DO NOT guess or hallucinate mandatory fields. If the user hasn't mentioned budget, leave target_rent and max_rent as null.
        - If user mentions only one budget number, set both target_rent and max_rent to that value.
        
        Respond with a JSON matching the RequirementExtractionResponse schema.
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
            "acceptable_locations": requirement_data.acceptable_locations,
            "excluded_locations": requirement_data.excluded_locations,
            "work_location": requirement_data.work_location,
            "target_rent": requirement_data.target_rent,
            "max_rent": requirement_data.max_rent,
            "preferred_move_in_date": requirement_data.preferred_move_in_date,
            "latest_move_in_date": requirement_data.latest_move_in_date,
            "preferred_property_configurations": requirement_data.preferred_property_configurations,
            "additional_preferences": requirement_data.additional_preferences,
            "raw_requirement_text": raw_text,
            "core_preferences": {k: v.dict() for k,v in requirement_data.core_preferences.items()}
        }).execute()
        
        # Trigger background retroactive matching
        self.db.table("agent_jobs").insert({
            "job_type": "SEARCH_CREATED",
            "status": "PENDING",
            "payload": {"search_id": str(session.id)},
            "run_after": "now()"
        }).execute()

        return session
