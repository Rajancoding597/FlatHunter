from app.db.client import get_supabase_client
from app.matching.engine import MatchingEngine
from dataclasses import asdict
import json

def test_engine():
    db = get_supabase_client()
    engine = MatchingEngine()
    
    # Get an active search
    search_res = db.table("search_sessions").select("id").eq("status", "ACTIVE").order("created_at", desc=True).limit(1).execute()
    if not search_res.data:
        print("No active searches found.")
        return
        
    search_id = search_res.data[0]['id']
    req_res = db.table("search_requirements").select("*").eq("search_id", search_id).execute()
    if not req_res.data:
        print("No requirements for search")
        return
        
    search_req = req_res.data[0]
    
    # Get 3 listings
    listings_res = db.table("listings").select("*").limit(3).execute()
    
    for listing in listings_res.data:
        eval_res = engine.evaluate_match(search_req, listing)
        print(f"--- MATCH EVALUATION for listing {listing['id']} ---")
        print(json.dumps(asdict(eval_res), indent=2, default=str))
        print()

if __name__ == "__main__":
    test_engine()
