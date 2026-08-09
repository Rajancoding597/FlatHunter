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
   - `VISION_PROVIDER`: Explicit listing-vision provider, `gemini` (default) or `groq`
   - `GROQ_API_KEY`: Required when `VISION_PROVIDER=groq`
   - `GROQ_VISION_MODEL`: Defaults to `qwen/qwen3.6-27b`
   - `GEMINI_VISION_MODEL`: Defaults to `gemini-2.5-flash-lite`

   Groq example:

   ```env
   VISION_PROVIDER=groq
   GROQ_API_KEY=your-key
   GROQ_VISION_MODEL=qwen/qwen3.6-27b
   ```

   Gemini example:

   ```env
   VISION_PROVIDER=gemini
   GEMINI_API_KEY=your-key
   GEMINI_VISION_MODEL=gemini-2.5-flash-lite
   ```

   Vision selection is independent from `LLM_PROVIDER`; there is no automatic
   provider fallback. The `/addlisting` flow sends all information screenshots
   for one property in one extraction request. Property gallery photos remain
   media and are not sent to vision models in V0. Provider output is validated
   as `FlatHunterExtractionV1`, unknown values remain null, and every successful
   extraction remains a draft until admin approval.

3. **Running the Application**
   For local development (runs FastAPI server + Telegram long polling bot):
   ```bash
   python -m app.main
   ```

## Renter Conversation

Renter mode accepts both Telegram commands and natural English. Renters can provide or
edit requirements, ask what has been collected, check search status and matches, discuss
property details, set visit availability, and pause, resume, or cancel a search. Questions
asked during requirement collection do not discard the current flow. Destructive or
core-criteria changes require confirmation through buttons or a natural yes/no reply.

Incomplete conversations and pending confirmations use in-memory Telegram FSM storage in
V0, so they reset when the bot process restarts. Saved drafts and live searches remain in
Supabase.

## Next Steps
- Apply Supabase migrations (when available)
- Implement Admin property ingestion flow
