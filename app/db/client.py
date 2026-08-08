from supabase import create_client, Client
from app.config import settings

def get_supabase_client() -> Client:
    # Use the service key for admin/backend privileges
    return create_client(
        settings.supabase_url,
        settings.supabase_service_key
    )

# Note: For async operations, we might consider using an async wrapper or an async Postgres driver like asyncpg depending on future needs.
