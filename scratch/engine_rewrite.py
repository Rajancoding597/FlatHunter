import logging
import json
from uuid import UUID
from typing import Dict, Any, Tuple, List
from dataclasses import dataclass, field, asdict
from app.db.client import get_supabase_client
from app.common.enums import MatchStatus
from app.common.tracer import tracer

logger = logging.getLogger(__name__)

@dataclass
class MatchEvaluation:
    status: MatchStatus
    fit_score: float = 0.0
    information_completeness: float = 0.0
    hard_rejection_reasons: List[str] = field(default_factory=list)
    positive_reasons: List[str] = field(default_factory=list)
    missing_information: List[str] = field(default_factory=list)
    soft_context_evaluation: Dict[str, Any] = field(default_factory=dict)

class MatchingEngine:
    def __init__(self):
        self.db = get_supabase_client()

    def evaluate_match(self, search_req: Dict[str, Any], listing: Dict[str, Any]) -> MatchEvaluation:
        eval_result = MatchEvaluation(status=MatchStatus.POSSIBLE_MATCH)
        listing_id = listing.get('id')
        search_id = search_req.get('search_id')
        
        # ---------------------------------------------------------
        # 1. Hard Constraints
        # ---------------------------------------------------------
        listing_type = listing.get('listing_type')
        pref_types = search_req.get('listing_types', [])
        if pref_types and listing_type not in pref_types:
            eval_result.hard_rejection_reasons.append(f"Listing type {listing_type} is not acceptable. Preferred: {pref_types}")

        locality = listing.get('locality', '').lower()
        city = listing.get('city', '').lower()
        location_text = f"{locality}, {city}"
        
        pref_locs = [loc.lower() for loc in search_req.get('preferred_locations', [])]
        acc_locs = [loc.lower() for loc in search_req.get('acceptable_locations', [])]
        exc_locs = [loc.lower() for loc in search_req.get('excluded_locations', [])]
        
        if exc_locs and any(loc in locality or loc in city for loc in exc_locs):
            eval_result.hard_rejection_reasons.append(f"Location {location_text} is explicitly excluded.")
            
        all_allowed = pref_locs + acc_locs
        if all_allowed and not any(loc in locality or loc in city for loc in all_allowed):
             eval_result.hard_rejection_reasons.append(f"Location {location_text} is not in preferred or acceptable locations.")

        rent = listing.get('rent')
        max_rent = search_req.get('max_rent', 999999)
        if rent is not None and rent > max_rent:
            eval_result.hard_rejection_reasons.append(f"Rent {rent} exceeds maximum budget of {max_rent}.")
            
        avail = listing.get('availability_status')
        if avail == 'UNAVAILABLE':
            eval_result.hard_rejection_reasons.append("Listing is explicitly UNAVAILABLE.")

        core_prefs = search_req.get('core_preferences', {})
        for pref_key, pref_val in core_prefs.items():
            importance = pref_val.get('importance') if isinstance(pref_val, dict) else None
            if importance == 'REQUIRED':
                listing_val = listing.get(pref_key)
                if listing_val is False:
                    eval_result.hard_rejection_reasons.append(f"Required preference '{pref_key}' is explicitly false in listing.")
                elif listing_val is None:
                    pass # Unknown does not reject, will be handled by completeness

        # ---------------------------------------------------------
        # 2. Information Completeness
        # ---------------------------------------------------------
        total_info_weight = 0
        known_info_weight = 0
        
        def add_info_check(name, is_known, weight):
            nonlocal total_info_weight, known_info_weight
            total_info_weight += weight
            if is_known:
                known_info_weight += weight
            else:
                eval_result.missing_information.append(name)
                
        add_info_check("Rent", rent is not None, 20)
        add_info_check("Availability", avail == 'AVAILABLE', 20) # 'UNKNOWN' or 'STALE' are not fresh
        add_info_check("Location", bool(locality or city), 15)
        add_info_check("Listing Type", listing_type is not None, 10)
        
        for pref_key, pref_val in core_prefs.items():
            importance = pref_val.get('importance') if isinstance(pref_val, dict) else None
            if importance == 'REQUIRED':
                add_info_check(f"Required amenity ({pref_key})", listing.get(pref_key) is not None, 15)
                
        eval_result.information_completeness = (known_info_weight / total_info_weight) * 100 if total_info_weight > 0 else 100

        # ---------------------------------------------------------
        # 3. Fit Score Calculation
        # ---------------------------------------------------------
        total_fit_weight = 0
        earned_fit_score = 0
        
        def add_fit_score(name, fraction, weight):
            nonlocal total_fit_weight, earned_fit_score
            total_fit_weight += weight
            earned_fit_score += (fraction * weight)
            if fraction >= 0.8:
                eval_result.positive_reasons.append(f"{name}: Excellent fit")
                
        # Location Fit (Weight: 30)
        if all_allowed:
            if pref_locs and any(loc in locality or loc in city for loc in pref_locs):
                add_fit_score("Location", 1.0, 30)
            elif acc_locs and any(loc in locality or loc in city for loc in acc_locs):
                add_fit_score("Location", 0.5, 30)
                
        # Budget Fit (Weight: 30)
        if rent is not None:
            target_rent = search_req.get('target_rent') or max_rent
            if rent <= target_rent:
                add_fit_score("Budget", 1.0, 30)
            else:
                if max_rent > target_rent:
                    frac = 0.5 + 0.5 * ((max_rent - rent) / (max_rent - target_rent))
                    add_fit_score("Budget", frac, 30)
                else:
                    add_fit_score("Budget", 0.5, 30)
                    
        # Preference Fit (Weight: 20)
        prefs_to_eval = [k for k in core_prefs.keys() if listing.get(k) is not None]
        if prefs_to_eval:
            weight_per_pref = 20 / len(prefs_to_eval)
            for p in prefs_to_eval:
                val = listing.get(p)
                if val is True:
                    add_fit_score(f"Amenity ({p})", 1.0, weight_per_pref)
                elif val is False:
                    add_fit_score(f"Amenity ({p})", 0.0, weight_per_pref)
                    
        if total_fit_weight > 0:
            eval_result.fit_score = (earned_fit_score / total_fit_weight) * 100
        else:
            eval_result.fit_score = 0.0

        # ---------------------------------------------------------
        # 4. Classification
        # ---------------------------------------------------------
        if eval_result.hard_rejection_reasons:
            eval_result.status = MatchStatus.REJECTED
        else:
            core_evidence_met = (listing_type is not None and (locality or city) and rent is not None)
            
            if not core_evidence_met:
                eval_result.status = MatchStatus.NEEDS_QUALIFICATION
            elif eval_result.fit_score >= 80 and eval_result.information_completeness >= 70 and avail == 'AVAILABLE':
                eval_result.status = MatchStatus.STRONG_MATCH
            elif eval_result.fit_score >= 70 and (eval_result.information_completeness < 70 or avail in ['UNKNOWN', 'STALE']):
                eval_result.status = MatchStatus.NEEDS_QUALIFICATION
            else:
                eval_result.status = MatchStatus.POSSIBLE_MATCH
                
        logger.debug(f"Evaluated Match: {listing_id} vs {search_id} | Status: {eval_result.status} | Fit: {eval_result.fit_score:.1f}% | Complete: {eval_result.information_completeness:.1f}%")
        return eval_result

    def process_new_listing(self, listing_id: str):
        res = self.db.table("listings").select("*").eq("id", listing_id).execute()
        if not res.data: return
        listing = res.data[0]
        
        active_searches = self.db.table("search_sessions").select("id").eq("status", "ACTIVE").execute()
        for search in active_searches.data:
            search_id = search['id']
            req_res = self.db.table("search_requirements").select("*").eq("search_id", search_id).execute()
            if not req_res.data: continue
            
            eval_res = self.evaluate_match(req_res.data[0], listing)
            self._upsert_match(search_id, listing_id, eval_res)

    def process_new_search(self, search_id: str):
        tracer.log_event("RETROACTIVE_MATCHING_STARTED", payload={"search_id": search_id})
        
        req_res = self.db.table("search_requirements").select("*").eq("search_id", search_id).execute()
        if not req_res.data: return
        req = req_res.data[0]
        
        listings_res = self.db.table("listings").select("*").neq("availability_status", "UNAVAILABLE").execute()
        
        matches_found = 0
        for listing in listings_res.data:
            eval_res = self.evaluate_match(req, listing)
            
            # Upsert the match regardless of status so admin can see rejected reasons
            self._upsert_match(search_id, listing['id'], eval_res)
            
            if eval_res.status in [MatchStatus.STRONG_MATCH, MatchStatus.POSSIBLE_MATCH, MatchStatus.NEEDS_QUALIFICATION]:
                matches_found += 1
                
        tracer.log_event("RETROACTIVE_MATCHING_COMPLETED", payload={"search_id": search_id, "matches_found": matches_found})

    def _upsert_match(self, search_id: str, listing_id: str, eval_res: MatchEvaluation):
        # Check if match already exists to avoid duplicate notifications
        existing = self.db.table("matches").select("status").eq("search_id", search_id).eq("listing_id", listing_id).execute()
        old_status = existing.data[0]['status'] if existing.data else None
        
        # Upsert match
        match_data = {
            "search_id": search_id,
            "listing_id": listing_id,
            "status": eval_res.status.value,
            "fit_score": eval_res.fit_score,
            "information_completeness": eval_res.information_completeness,
            "hard_rejection_reasons": eval_res.hard_rejection_reasons,
            "positive_reasons": eval_res.positive_reasons,
            "missing_information": eval_res.missing_information,
            "soft_context_evaluation": eval_res.soft_context_evaluation
        }
        
        # Note: Supabase Python client upsert syntax requires defining the conflict columns if they differ from primary key, 
        # but here we can just use upsert with on_conflict
        try:
            self.db.table("matches").upsert(match_data, on_conflict="search_id,listing_id").execute()
        except Exception as e:
            logger.error(f"Failed to upsert match {search_id}-{listing_id}: {e}")
            
        # Send Notification if transitioning to STRONG_MATCH for the first time
        if eval_res.status == MatchStatus.STRONG_MATCH and old_status != "STRONG_MATCH":
            self.db.table("agent_jobs").insert({
                "job_type": "SEND_RENTER_NOTIFICATION",
                "status": "PENDING",
                "payload": {"search_id": search_id, "listing_id": listing_id},
                "run_after": "now()"
            }).execute()
