from datetime import datetime
from uuid import UUID
from app.db.client import get_supabase_client
from app.common.enums import VisitStatus

class SchedulingService:
    def __init__(self, db=None): self.db=db or get_supabase_client()
    def propose_visit(self, search_id: UUID, listing_id: UUID, contact_id: UUID, proposed_start: datetime) -> UUID:
        r=self.db.table("visits").insert({"search_id":str(search_id),"listing_id":str(listing_id),"contact_id":str(contact_id),"status":VisitStatus.AWAITING_RENTER_CONFIRMATION.value,"proposed_start":proposed_start.isoformat()}).execute(); return r.data[0]['id']
    def confirm_visit(self, visit_id: UUID):
        visit=self.db.table("visits").select("proposed_start,status").eq("id",str(visit_id)).execute()
        if not visit.data: raise ValueError("Visit was not found")
        if visit.data[0]['status'] != VisitStatus.AWAITING_RENTER_CONFIRMATION.value: raise ValueError("Visit is not awaiting renter confirmation")
        self.db.table("visits").update({"status":VisitStatus.CONFIRMED.value,"confirmed_start":visit.data[0]['proposed_start'],"confirmed_at":"now()"}).eq("id",str(visit_id)).execute()
    def cancel_visit(self, visit_id: UUID): self.db.table("visits").update({"status":VisitStatus.CANCELLED.value,"cancelled_at":"now()"}).eq("id",str(visit_id)).execute()
    async def parse_and_save_availability(self,user_id:UUID,search_id:UUID,text:str):
        from app.llm.gemini import get_llm_provider
        from app.scheduling.schemas import RenterAvailabilityExtraction
        parsed=await get_llm_provider().generate_structured(f'Parse visit availability: "{text}"',RenterAvailabilityExtraction)
        self.db.table("renter_availability").insert({"user_id":str(user_id),"search_id":str(search_id) if search_id else None,"general_windows":[w.model_dump() for w in parsed.general_windows]}).execute()