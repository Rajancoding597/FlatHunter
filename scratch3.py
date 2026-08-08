from app.db.client import get_supabase_client
import json

def get_search_req():
    db = get_supabase_client()
    search_id = "2e802bdf-0385-403d-9cb9-2d045307d9b3"
    res = db.table("search_requirements").select("*").eq("search_id", search_id).execute()
    
    if res.data:
        print(json.dumps(res.data[0], indent=2))
    else:
        print("No requirements found.")

if __name__ == "__main__":
    get_search_req()
