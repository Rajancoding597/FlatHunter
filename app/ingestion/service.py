import json
from uuid import UUID
from app.db.client import get_supabase_client
from app.llm.gemini import get_llm_provider
from app.ingestion.schemas import ListingExtractionResponse
from app.common.enums import IngestionStatus, ContentType, AvailabilityStatus

class IngestionService:
    def __init__(self):
        self.db = get_supabase_client()
        self.llm = get_llm_provider()

    def create_session(self, admin_id: UUID, mode: str = "SINGLE") -> UUID:
        res = self.db.table("ingestion_sessions").insert({
            "admin_user_id": str(admin_id),
            "mode": mode,
            "status": IngestionStatus.COLLECTING_INFO.value
        }).execute()
        return res.data[0]['id']

    def add_text_input(self, session_id: UUID, text: str):
        self.db.table("ingestion_inputs").insert({
            "ingestion_session_id": str(session_id),
            "input_type": "TEXT",
            "text_content": text
        }).execute()
        
    def add_image_input(self, session_id: UUID, b64_data: str):
        self.db.table("ingestion_inputs").insert({
            "ingestion_session_id": str(session_id),
            "input_type": "IMAGE",
            "text_content": b64_data
        }).execute()

    async def complete_session_and_extract(self, session_id: UUID) -> UUID:
        import base64
        # Mark as extracting
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.EXTRACTING.value}).eq("id", str(session_id)).execute()
        
        # Gather inputs
        inputs = self.db.table("ingestion_inputs").select("*").eq("ingestion_session_id", str(session_id)).execute()
        
        prompt_parts = ["Extract the property details from the following information:"]
        for inp in inputs.data:
            if inp.get('input_type') == 'TEXT' and inp.get('text_content'):
                prompt_parts.append(inp['text_content'])
            elif inp.get('input_type') == 'IMAGE' and inp.get('text_content'):
                prompt_parts.append({
                    "mime_type": "image/jpeg",
                    "data": base64.b64decode(inp['text_content'])
                })
        
        try:
            parsed = await self.llm.generate_structured(prompt_parts, ListingExtractionResponse)
            
            # Save Draft
            draft_res = self.db.table("listing_drafts").insert({
                "ingestion_session_id": str(session_id),
                "content_type": ContentType.PROPERTY_LISTING.value,
                "canonical_payload": parsed.dict(),
                "extraction_status": "SUCCESS"
            }).execute()
            
            self.db.table("ingestion_sessions").update({"status": IngestionStatus.READY_FOR_APPROVAL.value}).eq("id", str(session_id)).execute()
            
            return draft_res.data[0]['id']
            
        except Exception as e:
            raise ValueError(f"Failed to extract listing: {e}")

    def approve_draft(self, draft_id: UUID) -> UUID:
        draft = self.db.table("listing_drafts").select("*").eq("id", str(draft_id)).execute().data[0]
        payload = draft["canonical_payload"]
        
        import datetime
        available_from = payload.get("available_from")
        # Defensively parse date to ensure it doesn't crash the database insert
        try:
            if available_from and available_from.lower() == "immediate":
                available_from = datetime.date.today().isoformat()
            elif available_from:
                # Try to parse YYYY-MM-DD
                datetime.datetime.strptime(available_from, "%Y-%m-%d")
        except ValueError:
            available_from = None
        
        # Create Listing
        listing_res = self.db.table("listings").insert({
            "listing_type": payload.get("listing_type", "ENTIRE_PROPERTY"),
            "city": payload.get("city", "Hyderabad"),
            "locality": payload.get("locality", "Unknown"),
            "rent": payload.get("rent"),
            "deposit": payload.get("deposit"),
            "maintenance": payload.get("maintenance"),
            "available_from": available_from,
            "availability_status": AvailabilityStatus.AVAILABLE.value,
            "created_from_draft_id": str(draft_id)
        }).execute()
        
        listing_id = listing_res.data[0]["id"]
        
        # Trigger background matching job
        self.db.table("agent_jobs").insert({
            "job_type": "LISTING_CREATED",
            "status": "PENDING",
            "payload": {"listing_id": listing_id},
            "run_after": "now()"
        }).execute()
        
        # Mark draft and session
        self.db.table("listing_drafts").update({"extraction_status": "APPROVED"}).eq("id", str(draft_id)).execute()
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.APPROVED.value}).eq("id", draft["ingestion_session_id"]).execute()
        
        return listing_id

    async def complete_bulk_session_and_extract(self, session_id: UUID) -> list[UUID]:
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.EXTRACTING.value}).eq("id", str(session_id)).execute()
        
        inputs = self.db.table("ingestion_inputs").select("*").eq("ingestion_session_id", str(session_id)).execute()
        text_corpus = "\n".join([inp['text_content'] for inp in inputs.data if inp['text_content']])
        
        # Split by '---' to simulate separating multiple listings
        listings_text = [t.strip() for t in text_corpus.split("---") if t.strip()]
        draft_ids = []
        
        for text in listings_text:
            prompt = f"Extract the property details from the following raw text/notes:\n\"{text}\""
            try:
                parsed = await self.llm.generate_structured(prompt, ListingExtractionResponse)
                draft_res = self.db.table("listing_drafts").insert({
                    "ingestion_session_id": str(session_id),
                    "content_type": ContentType.PROPERTY_LISTING.value,
                    "canonical_payload": parsed.dict(),
                    "extraction_status": "SUCCESS"
                }).execute()
                draft_ids.append(draft_res.data[0]['id'])
            except Exception as e:
                print(f"Skipping a listing due to error: {e}")
                
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.READY_FOR_APPROVAL.value}).eq("id", str(session_id)).execute()
        return draft_ids
