import json
from uuid import UUID
from app.db.client import get_supabase_client
from app.llm.gemini import GeminiProvider
from app.common.enums import ConversationStatus

class QualificationService:
    def __init__(self):
        self.db = get_supabase_client()
        self.llm = GeminiProvider()

    def start_conversation(self, search_id: UUID, listing_id: UUID, contact_id: UUID) -> UUID:
        res = self.db.table("conversations").insert({
            "search_id": str(search_id),
            "listing_id": str(listing_id),
            "contact_id": str(contact_id),
            "status": ConversationStatus.APPROVED_FOR_CONTACT.value
        }).execute()
        return res.data[0]['id']

    async def generate_initial_outreach(self, conversation_id: UUID) -> str:
        # Fetch conversation, listing and search requirements
        conv = self.db.table("conversations").select("*").eq("id", str(conversation_id)).execute().data[0]
        search_req = self.db.table("search_requirements").select("*").eq("search_id", conv['search_id']).execute().data[0]
        
        prompt = f"""
        Draft a polite initial message to the property owner/broker.
        Context: The renter is looking for {search_req['listing_types']} with budget {search_req['target_rent']}.
        Goal: Ask if the property is still available and mention we are interested.
        Keep it very short.
        """
        response = await self.llm.generate_text(prompt)
        
        # Save message
        self.db.table("messages").insert({
            "conversation_id": str(conversation_id),
            "channel_type": "TELEGRAM", # Defaulting for simulation
            "direction": "OUTBOUND",
            "text": response.strip()
        }).execute()
        
        self.db.table("conversations").update({"status": ConversationStatus.CONTACTED.value}).eq("id", str(conversation_id)).execute()
        
        return response.strip()

    async def process_inbound_reply(self, conversation_id: UUID, text: str):
        self.db.table("messages").insert({
            "conversation_id": str(conversation_id),
            "channel_type": "TELEGRAM",
            "direction": "INBOUND",
            "text": text
        }).execute()
        
        # Determine next state and extract facts
        prompt = f"""
        Given the property owner's reply: "{text}"
        Are they saying the property is available? Are there any red flags?
        Extract any concrete facts mentioned (rent, deposit, parking).
        If available but we don't know the deposit, draft a follow-up question.
        """
        from app.qualification.schemas import ReplyClassification
        
        try:
            parsed = await self.llm.generate_structured(prompt, ReplyClassification)
        except Exception as e:
            # Fallback on failure
            self.db.table("conversations").update({"status": ConversationStatus.QUALIFYING.value}).eq("id", str(conversation_id)).execute()
            return
            
        outcome = parsed.availability
        facts = parsed.facts.dict(exclude_unset=True, exclude_none=True)
        
        # Update listing facts if any were extracted
        if facts:
            conv = self.db.table("conversations").select("listing_id").eq("id", str(conversation_id)).execute().data[0]
            self.db.table("listings").update(facts).eq("id", conv["listing_id"]).execute()
        
        if outcome == "AVAILABLE":
            if parsed.proposed_visit_time:
                from app.scheduling.service import SchedulingService
                from dateutil.parser import parse
                
                sched_service = SchedulingService()
                
                try:
                    visit_date = parse(parsed.proposed_visit_time)
                    conv_data = self.db.table("conversations").select("*").eq("id", str(conversation_id)).execute().data[0]
                    visit_id = sched_service.propose_visit(
                        conv_data['search_id'],
                        conv_data['listing_id'],
                        conv_data['contact_id'],
                        visit_date
                    )
                    
                    self.db.table("agent_jobs").insert({
                        "job_type": "PROPOSE_VISIT_TO_RENTER",
                        "status": "PENDING",
                        "payload": {"visit_id": str(visit_id), "search_id": conv_data['search_id']},
                        "run_after": "now()"
                    }).execute()
                    
                    self.db.table("conversations").update({"status": ConversationStatus.READY_FOR_SCHEDULING.value}).eq("id", str(conversation_id)).execute()
                except Exception as e:
                    print(f"Failed to parse visit time: {e}")
                    self.db.table("conversations").update({"status": ConversationStatus.READY_FOR_SCHEDULING.value}).eq("id", str(conversation_id)).execute()
                    
            elif parsed.next_question:
                # We need to ask more info
                self.db.table("messages").insert({
                    "conversation_id": str(conversation_id),
                    "channel_type": "TELEGRAM",
                    "direction": "OUTBOUND",
                    "text": parsed.next_question
                }).execute()
                self.db.table("conversations").update({"status": ConversationStatus.QUALIFYING.value}).eq("id", str(conversation_id)).execute()
            else:
                self.db.table("conversations").update({"status": ConversationStatus.READY_FOR_SCHEDULING.value}).eq("id", str(conversation_id)).execute()
        elif outcome == "UNAVAILABLE":
            self.db.table("conversations").update({"status": ConversationStatus.CLOSED.value}).eq("id", str(conversation_id)).execute()
        else:
            self.db.table("conversations").update({"status": ConversationStatus.QUALIFYING.value}).eq("id", str(conversation_id)).execute()
