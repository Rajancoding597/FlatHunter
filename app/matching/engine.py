from uuid import UUID
from typing import Dict, Any, Tuple
from app.db.client import get_supabase_client
from app.common.enums import MatchStatus

class MatchingEngine:
    def __init__(self):
        self.db = get_supabase_client()

    def evaluate_match(self, search_req: Dict[str, Any], listing: Dict[str, Any]) -> Tuple[MatchStatus, float]:
        # Very simplified deterministic match
        rejections = []
        
        # Hard constraint: rent limit
        if listing['rent'] > search_req['max_rent']:
            rejections.append("Rent exceeds maximum allowed.")
            
        # Hard constraint: listing type
        if listing['listing_type'] not in search_req['listing_types']:
            rejections.append(f"Listing type {listing['listing_type']} is not preferred.")
            
        if rejections:
            return MatchStatus.REJECTED, 0.0
            
        # Calculate fit score
        score = 0.5
        if listing['rent'] <= search_req['target_rent']:
            score += 0.3
            
        return MatchStatus.STRONG_MATCH, score

    def process_new_listing(self, listing_id: str):
        # Fetch listing
        res = self.db.table("listings").select("*").eq("id", listing_id).execute()
        if not res.data:
            return
        listing = res.data[0]
        
        # Fetch active searches
        active_searches = self.db.table("search_sessions").select("id").eq("status", "ACTIVE").execute()
        
        for search in active_searches.data:
            search_id = search['id']
            req_res = self.db.table("search_requirements").select("*").eq("search_id", search_id).execute()
            if not req_res.data:
                continue
            
            req = req_res.data[0]
            
            status, score = self.evaluate_match(req, listing)
            
            # Save Match
            self.db.table("matches").insert({
                "search_id": search_id,
                "listing_id": listing_id,
                "status": status.value,
                "fit_score": score
            }).execute()
            
            # Send Notification if STRONG_MATCH (Stub)
            if status == MatchStatus.STRONG_MATCH:
                self.db.table("agent_jobs").insert({
                    "job_type": "SEND_RENTER_NOTIFICATION",
                    "status": "PENDING",
                    "payload": {"search_id": search_id, "listing_id": listing_id},
                    "run_after": "now()"
                }).execute()
