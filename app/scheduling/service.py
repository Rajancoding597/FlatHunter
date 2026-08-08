from uuid import UUID
from datetime import datetime
from app.db.client import get_supabase_client
from app.common.enums import VisitStatus, ConversationStatus

class SchedulingService:
    def __init__(self):
        self.db = get_supabase_client()

    def propose_visit(self, search_id: UUID, listing_id: UUID, contact_id: UUID, proposed_start: datetime) -> UUID:
        res = self.db.table("visits").insert({
            "search_id": str(search_id),
            "listing_id": str(listing_id),
            "contact_id": str(contact_id),
            "status": VisitStatus.PROPOSED.value,
            "proposed_start": proposed_start.isoformat()
        }).execute()
        return res.data[0]['id']

    def confirm_visit(self, visit_id: UUID):
        self.db.table("visits").update({
            "status": VisitStatus.CONFIRMED.value,
            "confirmed_start": "proposed_start", # Simplified
            "confirmed_at": "now()"
        }).eq("id", str(visit_id)).execute()

    def cancel_visit(self, visit_id: UUID):
        self.db.table("visits").update({
            "status": VisitStatus.CANCELLED.value,
            "cancelled_at": "now()"
        }).eq("id", str(visit_id)).execute()

    async def parse_and_save_availability(self, user_id: UUID, search_id: UUID, text: str):
        from app.llm.gemini import get_llm_provider
        from app.scheduling.schemas import RenterAvailabilityExtraction
        
        llm = get_llm_provider()
        prompt = f"""
        Parse the user's availability for flat visits from the following text:
        "{text}"
        """
        parsed = await llm.generate_structured(prompt, RenterAvailabilityExtraction)
        
        self.db.table("renter_availability").insert({
            "user_id": str(user_id),
            "search_id": str(search_id) if search_id else None,
            "general_windows": [w.dict() for w in parsed.general_windows]
        }).execute()

