import asyncio
import logging
from app.db.client import get_supabase_client
from app.matching.engine import MatchingEngine

logger = logging.getLogger(__name__)

class JobWorker:
    def __init__(self):
        self.db = get_supabase_client()
        self.matching_engine = MatchingEngine()

    async def run(self):
        logger.info("Starting background job worker...")
        
        # Start email polling task
        asyncio.create_task(self.poll_emails_loop())
        
        while True:
            try:
                # Fetch one pending job
                res = self.db.table("agent_jobs").select("*").eq("status", "PENDING").order("created_at").limit(1).execute()
                if not res.data:
                    await asyncio.sleep(5)
                    continue
                
                job = res.data[0]
                job_id = job['id']
                
                # Mark as running
                self.db.table("agent_jobs").update({"status": "RUNNING"}).eq("id", job_id).execute()
                
                await self.process_job(job)
                
                # Mark as succeeded
                self.db.table("agent_jobs").update({"status": "SUCCEEDED"}).eq("id", job_id).execute()
            except Exception as e:
                logger.error(f"Error processing job {job.get('id', 'unknown')}: {e}")
                if 'job_id' in locals():
                    self.db.table("agent_jobs").update({"status": "FAILED", "last_error": str(e)}).eq("id", job_id).execute()
                await asyncio.sleep(5)

    async def poll_emails_loop(self):
        from app.communications.email import EmailAdapter
        from app.qualification.service import QualificationService
        
        email_adapter = EmailAdapter()
        qual_service = QualificationService()
        
        while True:
            try:
                inbound_msgs = await email_adapter.poll_inbound()
                for msg in inbound_msgs:
                    conv_id = msg.get("conversation_id")
                    if conv_id:
                        logger.info(f"Processing inbound email for conversation {conv_id}")
                        await qual_service.process_inbound_reply(conv_id, msg.get("text", ""))
                    else:
                        logger.warning(f"Uncorrelated inbound email from {msg.get('from')}")
            except Exception as e:
                logger.error(f"Error in email polling loop: {e}")
                
            await asyncio.sleep(60) # Poll every 60 seconds

    async def process_job(self, job: dict):
        job_type = job['job_type']
        payload = job['payload']
        
        if job_type == "LISTING_CREATED":
            listing_id = payload['listing_id']
            self.matching_engine.process_new_listing(listing_id)
            
        elif job_type == "SEND_RENTER_NOTIFICATION":
            search_id = payload['search_id']
            listing_id = payload['listing_id']
            
            # Fetch user ID from search_id
            search_res = self.db.table("search_sessions").select("user_id").eq("id", search_id).execute()
            if not search_res.data:
                return
            
            user_res = self.db.table("users").select("telegram_user_id").eq("id", search_res.data[0]['user_id']).execute()
            if not user_res.data:
                return
            
            telegram_user_id = user_res.data[0]['telegram_user_id']
            
            # Fetch listing details
            listing_res = self.db.table("listings").select("*").eq("id", listing_id).execute()
            if not listing_res.data:
                return
            listing = listing_res.data[0]
            
            config = listing.get('property_configuration') or listing.get('listing_type', 'Property')
            loc = listing.get('locality') or listing.get('location_text') or listing.get('city') or 'Unknown'
            rent = f"₹{listing.get('rent'):,}" if listing.get('rent') else "Not specified"
            furnishing = listing.get('furnishing') or "Not specified"
            
            # Message to send
            message_text = (
                f"🔥 <b>We found a STRONG MATCH for your search!</b>\n\n"
                f"🌟 <b>{config} in {loc}</b>\n"
                f"💰 <b>Rent:</b> {rent}\n"
                f"🛋️ <b>Furnishing:</b> {furnishing}\n\n"
                f"Do you want us to contact the landlord on your behalf?"
            )
            
            # Initialize bot just for sending message
            from aiogram import Bot
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            from app.config import settings
            bot = Bot(token=settings.telegram_bot_token)
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Contact Them", callback_data=f"contact_{search_id}_{listing_id}")]
            ])
            
            import asyncio
            try:
                await bot.send_message(chat_id=telegram_user_id, text=message_text, reply_markup=keyboard, parse_mode="HTML")
            finally:
                await bot.session.close()
            
            logger.info(f"Sent Telegram notification to {telegram_user_id} for match {listing_id}")
            
        elif job_type == "PROPOSE_VISIT_TO_RENTER":
            visit_id = payload['visit_id']
            search_id = payload['search_id']
            
            # Fetch user ID from search_id
            search_res = self.db.table("search_sessions").select("user_id").eq("id", search_id).execute()
            if not search_res.data:
                return
                
            user_res = self.db.table("users").select("telegram_user_id").eq("id", search_res.data[0]['user_id']).execute()
            if not user_res.data:
                return
                
            telegram_user_id = user_res.data[0]['telegram_user_id']
            
            # Fetch visit details
            visit = self.db.table("visits").select("*").eq("id", visit_id).execute().data[0]
            proposed_start = visit['proposed_start']
            
            message_text = f"📅 The landlord has proposed a visit time:\n{proposed_start}\n\nDoes this work for you?"
            
            from aiogram import Bot
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            from app.config import settings
            bot = Bot(token=settings.telegram_bot_token)
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Confirm Visit", callback_data=f"visit_confirm_{visit_id}")],
                [InlineKeyboardButton(text="Decline / Propose New Time", callback_data=f"visit_decline_{visit_id}")]
            ])
            
            import asyncio
            try:
                await bot.send_message(chat_id=telegram_user_id, text=message_text, reply_markup=keyboard)
            finally:
                await bot.session.close()
                
        elif job_type == "EMAIL_CONFIRM_VISIT":
            visit_id = payload['visit_id']
            visit = self.db.table("visits").select("*").eq("id", visit_id).execute().data[0]
            
            # Fetch contact email
            contact_channel = self.db.table("contact_channels").select("value").eq("contact_id", visit['contact_id']).eq("type", "EMAIL").execute()
            
            if contact_channel.data:
                email = contact_channel.data[0]['value']
                from app.communications.email import EmailAdapter
                
                # Fetch conversation to get ID for threads
                conv = self.db.table("conversations").select("id").eq("search_id", visit['search_id']).eq("listing_id", visit['listing_id']).execute().data[0]
                
                adapter = EmailAdapter()
                await adapter.send(email, f"Hi,\n\nThe renter has confirmed the visit for {visit['confirmed_start']}. See you then!\n\nBest,\nFlatHunter", {"conversation_id": conv['id']})
                
        elif job_type == "SEARCH_CREATED":
            search_id = payload['search_id']
            logger.info(f"Processing SEARCH_CREATED for search {search_id}")
            self.matching_engine.process_new_search(search_id)
            
        else:
            logger.warning(f"Unknown job type: {job_type}")
