from app.db.client import get_supabase_client
import json

def check_search_status():
    db = get_supabase_client()
    # Search ID from the previous log
    search_id = "2e802bdf-0385-403d-9cb9-2d045307d9b3"
    res = db.table("search_sessions").select("*").eq("id", search_id).execute()
    
    print(f"--- Search {search_id} ---")
    if res.data:
        print(json.dumps(res.data[0], indent=2))
    else:
        print("Search not found.")

if __name__ == "__main__":
    check_search_status()
