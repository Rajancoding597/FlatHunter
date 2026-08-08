from app.db.client import get_supabase_client
import json

def fetch_data():
    db = get_supabase_client()
    print("--- LISTING (RENT > 0) ---")
    res = db.table("listings").select("*").gt("rent", 0).limit(2).execute()
    if res.data:
        for idx, item in enumerate(res.data):
            print(f"\n--- Item {idx + 1} ---")
            print(json.dumps(item, indent=2))
    else:
        print("No listings found with rent > 0.")
        
    print("\n--- LISTING DRAFTS (RECENT 2) ---")
    draft_res = db.table("listing_drafts").select("*").order("created_at", desc=True).limit(2).execute()
    if draft_res.data:
        for idx, item in enumerate(draft_res.data):
            print(f"\n--- Draft {idx + 1} ---")
            print(json.dumps(item, indent=2))
    else:
        print("No listing drafts found.")

if __name__ == "__main__":
    fetch_data()
