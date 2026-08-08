# FlatHunter V0

FlatHunter is a Telegram-first rental search concierge for Hyderabad.

## Project Setup

1. **Install Dependencies**
   Ensure you have Python 3.12+ installed.
   ```bash
   pip install -e .[dev]
   ```

2. **Configuration**
   Open the `.env.local` file and fill in your actual credentials:
   - `TELEGRAM_BOT_TOKEN`: Token from BotFather
   - `ADMIN_TELEGRAM_IDS`: Comma-separated list of Telegram User IDs for admins
   - `SUPABASE_URL`: Your Supabase project URL
   - `SUPABASE_SERVICE_KEY`: Your Supabase service role key
   - `GEMINI_API_KEY`: Google Gemini API key

3. **Running the Application**
   For local development (runs FastAPI server + Telegram long polling bot):
   ```bash
   python -m app.main
   ```

## Next Steps
- Apply Supabase migrations (when available)
- Implement Renter requirement collection flow
- Implement Admin property ingestion flow
