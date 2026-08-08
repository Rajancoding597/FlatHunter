import logging
from uuid import UUID
from typing import Dict, Any, Tuple
from app.db.client import get_supabase_client
from app.common.enums import MatchStatus

logger = logging.getLogger(__name__)

class MatchingEngine:
    def __init__(self):
        self.db = get_supabase_client()

    def evaluate_match(self, search_req: Dict[str, Any], listing: Dict[str, Any]) -> Tuple[MatchStatus, float, Dict[str, Any]]:
        rejections = []
        reasoning = {}
        
        listing_id = listing.get('id')
        search_id = search_req.get('search_id')
        
        # 1. Listing Type
        listing_type = listing.get('listing_type')
        pref_types = search_req.get('listing_types', [])
        if pref_types and listing_type not in pref_types:
            reasoning["listing_type"] = f"Rejected: {listing_type} not in {pref_types}"
            rejections.append(reasoning["listing_type"])
        else:
            reasoning["listing_type"] = "Passed"

        # 2. Location Check (basic substring match for now)
        locality = listing.get('locality', '').lower()
        city = listing.get('city', '').lower()
        pref_locs = [loc.lower() for loc in search_req.get('preferred_locations', [])]
        if pref_locs:
            if not any(loc in locality or loc in city for loc in pref_locs):
                reasoning["location"] = f"Rejected: {locality}, {city} not in {pref_locs}"
                rejections.append(reasoning["location"])
            else:
                reasoning["location"] = "Passed"
        else:
            reasoning["location"] = "Passed (no preferences)"

        # 3. Property Configuration (e.g. 3BHK)
        config = listing.get('property_configuration')
        pref_configs = search_req.get('preferred_property_configurations', [])
        if pref_configs and config:
            if config not in pref_configs:
                reasoning["configuration"] = f"Rejected: {config} not in {pref_configs}"
                rejections.append(reasoning["configuration"])
            else:
                reasoning["configuration"] = "Passed"
        else:
             reasoning["configuration"] = "Passed (no strict config check)"
            
        if rejections:
            logger.debug(f"Match REJECTED | Listing: {listing_id} | Search: {search_id} | Reasoning: {reasoning}")
            return MatchStatus.REJECTED, 0.0, reasoning
            
        # 4. Rent limit
        if listing.get('rent') is None:
            reasoning["rent"] = "Needs Qualification (No rent specified)"
            logger.debug(f"Match NEEDS QUALIFICATION | Listing: {listing_id} | Search: {search_id} | Reasoning: {reasoning}")
            return MatchStatus.NEEDS_QUALIFICATION, 0.0, reasoning
            
        if listing['rent'] > search_req.get('max_rent', 999999):
            reasoning["rent"] = f"Rejected: Rent {listing['rent']} > max {search_req['max_rent']}"
            rejections.append(reasoning["rent"])
            logger.debug(f"Match REJECTED | Listing: {listing_id} | Search: {search_id} | Reasoning: {reasoning}")
            return MatchStatus.REJECTED, 0.0, reasoning
        else:
            reasoning["rent"] = "Passed"
            
        # Calculate fit score
        score = 0.5
        if listing['rent'] <= search_req.get('target_rent', 0):
            score += 0.3
            reasoning["score"] = "Bonus for being under target rent"
            
        logger.debug(f"Match SUCCESS | Listing: {listing_id} | Search: {search_id} | Score: {score} | Reasoning: {reasoning}")
        return MatchStatus.STRONG_MATCH, score, reasoning

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
            
            status, score, reasoning = self.evaluate_match(req, listing)
            
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

    def process_new_search(self, search_id: str):
        from app.common.tracer import tracer
        
        # Log start
        tracer.log_event(
            event_type="RETROACTIVE_MATCHING_STARTED",
            override_search_id=search_id,
            payload={"search_id": search_id}
        )
        
        # Fetch search requirements
        req_res = self.db.table("search_requirements").select("*").eq("search_id", search_id).execute()
        if not req_res.data:
            return
        req = req_res.data[0]
        
        # Fetch all AVAILABLE listings
        listings_res = self.db.table("listings").select("*").eq("availability_status", "AVAILABLE").execute()
        listings = listings_res.data
        
        matches_found = 0
        
        for listing in listings:
            status, score, reasoning = self.evaluate_match(req, listing)
            
            # If it's a match, save it and notify
            if status in [MatchStatus.STRONG_MATCH, MatchStatus.POSSIBLE_MATCH, MatchStatus.NEEDS_QUALIFICATION]:
                matches_found += 1
                
                # Save Match
                self.db.table("matches").insert({
                    "search_id": search_id,
                    "listing_id": listing['id'],
                    "status": status.value,
                    "fit_score": score
                }).execute()
                
                # Send Notification if STRONG_MATCH (Stub)
                if status == MatchStatus.STRONG_MATCH:
                    self.db.table("agent_jobs").insert({
                        "job_type": "SEND_RENTER_NOTIFICATION",
                        "status": "PENDING",
                        "payload": {"search_id": search_id, "listing_id": listing['id']},
                        "run_after": "now()"
                    }).execute()
                    
        # Log completion
        tracer.log_event(
            event_type="RETROACTIVE_MATCHING_COMPLETED",
            override_search_id=search_id,
            payload={"search_id": search_id, "matches_found": matches_found}
        )
