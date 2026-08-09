import asyncio
from html import escape
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4
from app.db.client import get_supabase_client
from app.matching.engine import MatchingEngine

logger = logging.getLogger(__name__)

TELEGRAM_CALLBACK_DATA_MAX_BYTES = 64


def _match_action_keyboard(match_id: str):
    """Build renter actions using one UUID so Telegram's 64-byte limit is respected."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    details_data = f"details_match_{match_id}"
    contact_data = f"contact_match_{match_id}"
    skip_data = f"skip_match_{match_id}"
    for callback_data in (details_data, contact_data, skip_data):
        if len(callback_data.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_MAX_BYTES:
            raise ValueError("Match callback data exceeds Telegram's 64-byte limit")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View full property details", callback_data=details_data)],
        [InlineKeyboardButton(text="Contact owner / agent", callback_data=contact_data)],
        [InlineKeyboardButton(text="Not interested", callback_data=skip_data)],
    ])

class JobWorker:
    """Database-backed worker with atomic claims and bounded retries."""

    def __init__(self, db: Optional[Any] = None, matching_engine: Optional[MatchingEngine] = None, worker_id: Optional[str] = None):
        if db is None:
            db = get_supabase_client()
        self.db = db
        self.matching_engine = matching_engine or MatchingEngine(db=db)
        self.worker_id = worker_id or f"worker-{uuid4()}"

    def claim_next_job(self) -> Optional[dict]:
        response = self.db.rpc("claim_next_agent_job", {"p_worker_id": self.worker_id}).execute()
        return response.data[0] if response.data else None

    def _active_search_owner(
        self,
        search_id: str,
        *,
        expected_version: Any = None,
        notification_kind: str,
    ) -> Optional[str]:
        """Return the owner only while a queued renter notification is still current."""
        result = self.db.table("search_sessions").select("user_id,status,version").eq(
            "id", search_id
        ).limit(1).execute()
        if not result.data:
            logger.info(
                "Skipping renter notification because its search no longer exists",
                extra={"search_id": search_id, "notification_kind": notification_kind},
            )
            return None

        search = result.data[0]
        if search.get("status") != "ACTIVE":
            logger.info(
                "Skipping renter notification because its search is not active",
                extra={
                    "search_id": search_id,
                    "search_status": search.get("status"),
                    "notification_kind": notification_kind,
                },
            )
            return None

        if expected_version is not None:
            try:
                version_matches = int(search.get("version")) == int(expected_version)
            except (TypeError, ValueError):
                version_matches = False
            if not version_matches:
                logger.info(
                    "Skipping stale renter notification",
                    extra={
                        "search_id": search_id,
                        "expected_search_version": expected_version,
                        "current_search_version": search.get("version"),
                        "notification_kind": notification_kind,
                    },
                )
                return None

        user_id = search.get("user_id")
        return str(user_id) if user_id is not None else None

    async def run(self):
        logger.info("Starting background job worker", extra={"worker_id": self.worker_id})
        asyncio.create_task(self.poll_emails_loop())
        while True:
            await self.run_once()

    async def run_once(self) -> bool:
        job = self.claim_next_job()
        if job is None:
            await asyncio.sleep(5)
            return False
        try:
            await self.process_job(job)
            self.db.table("agent_jobs").update({
                "status": "SUCCEEDED",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "last_error": None,
            }).eq("id", job["id"]).eq("locked_by", self.worker_id).execute()
            logger.info("Agent job succeeded", extra={"job_id": job["id"], "job_type": job["job_type"]})
        except Exception as error:
            self._record_failure(job, error)
        return True

    def _record_failure(self, job: dict, error: Exception) -> None:
        attempts = int(job.get("attempts", 1))
        terminal = attempts >= 3
        update = {
            "status": "FAILED" if terminal else "PENDING",
            "last_error": str(error),
            "locked_at": None,
            "locked_by": None,
        }
        if not terminal:
            update["run_after"] = (datetime.now(timezone.utc) + timedelta(seconds=30 * attempts)).isoformat()
        self.db.table("agent_jobs").update(update).eq("id", job["id"]).eq("locked_by", self.worker_id).execute()
        logger.exception("Agent job failed", extra={"job_id": job["id"], "attempts": attempts, "terminal": terminal})

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

    async def send_initial_results(self, search_id: str) -> None:
        """Deliver a concise summary followed by the highest-ranked actionable cards."""
        search_result = self.db.table("search_sessions").select("user_id,status").eq("id", search_id).execute()
        if not search_result.data or search_result.data[0].get("status") != "ACTIVE":
            return
        user_result = self.db.table("users").select("telegram_user_id").eq("id", search_result.data[0]["user_id"]).execute()
        if not user_result.data:
            return
        matches = self.db.table("matches").select(
            "id,listing_id,status,fit_score,positive_reasons,missing_information"
        ).eq("search_id", search_id).in_("status", ["STRONG_MATCH", "NEEDS_QUALIFICATION"]).order("fit_score", desc=True).limit(5).execute()
        if not matches.data:
            return

        from aiogram import Bot
        from app.config import settings

        bot = Bot(token=settings.telegram_bot_token)
        chat_id = user_result.data[0]["telegram_user_id"]
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"<b>Initial results</b> — I found {len(matches.data)} promising property{'' if len(matches.data) == 1 else 'ies'} to review.",
                parse_mode="HTML",
            )
            for rank, match in enumerate(matches.data, start=1):
                listing_result = self.db.table("listings").select("*").eq("id", match["listing_id"]).execute()
                if not listing_result.data:
                    continue
                listing = listing_result.data[0]
                card = self.build_match_card(rank, match, listing)
                keyboard = _match_action_keyboard(str(match["id"]))
                media = self.db.table("listing_media").select("telegram_file_id").eq("listing_id", match["listing_id"]).order("sort_order").limit(1).execute()
                if media.data and media.data[0].get("telegram_file_id"):
                    await bot.send_photo(chat_id=chat_id, photo=media.data[0]["telegram_file_id"], caption=card, reply_markup=keyboard, parse_mode="HTML")
                else:
                    await bot.send_message(chat_id=chat_id, text=card, reply_markup=keyboard, parse_mode="HTML")
        finally:
            await bot.session.close()

    @staticmethod
    def build_match_card(rank: int, match: dict, listing: dict) -> str:
        configuration = escape(str(listing.get("property_configuration") or listing.get("listing_type") or "Property"))
        locality = escape(str(listing.get("locality") or listing.get("location_text") or listing.get("city") or "Location to confirm"))
        rent = f"₹{listing['rent']:,}" if listing.get("rent") is not None else "Rent to confirm"
        fit = round(float(match.get("fit_score") or 0))
        explanation = escape(", ".join(str(reason) for reason in (match.get("positive_reasons") or [])[:3]) or "Matches your saved search")
        qualification = ""
        if match.get("status") == "NEEDS_QUALIFICATION":
            gaps = escape(", ".join(str(item.get("field", "details")) for item in (match.get("missing_information") or [])[:2]))
            qualification = f"\n⏳ I will confirm: {gaps or 'key listing details'}"
        details = [f"💰 Rent: {rent}"]
        for label, field in (("Maintenance", "maintenance"), ("Deposit", "deposit"), ("Brokerage", "brokerage")):
            if listing.get(field) is not None:
                details.append(f"{label}: ₹{listing[field]:,}")
        if listing.get("furnishing"):
            details.append(f"Furnishing: {escape(str(listing['furnishing']).replace('_', ' ').title())}")
        if listing.get("available_from"):
            details.append(f"Available from: {escape(str(listing['available_from']))}")
        if listing.get("attached_bathroom") is not None:
            details.append(f"Attached bathroom: {'Yes' if listing['attached_bathroom'] else 'No'}")
        parking = []
        if listing.get("car_parking"):
            parking.append("car")
        if listing.get("bike_parking"):
            parking.append("bike")
        if parking:
            details.append(f"Parking: {' and '.join(parking).title()}")
        return "\n".join([
            f"<b>#{rank} · {fit}% fit</b>",
            f"<b>{configuration} in {locality}</b>",
            *details,
            f"Why it fits: {explanation}{qualification}",
            "",
            "Would you like FlatHunter to contact the owner or agent for you?",
        ])
    async def process_job(self, job: dict):
        job_type = job['job_type']
        payload = job['payload']
        
        if job_type in {"LISTING_CREATED", "LISTING_UPDATED"}:
            listing_id = payload['listing_id']
            self.matching_engine.process_new_listing(listing_id)
            
        elif job_type == "SEND_INITIAL_RESULTS":
            await self.send_initial_results(payload["search_id"])

        elif job_type == "SEND_RENTER_NOTIFICATION":
            search_id = payload['search_id']
            listing_id = payload['listing_id']
            expected_version = payload.get("search_version")

            owner_user_id = self._active_search_owner(
                search_id,
                expected_version=expected_version,
                notification_kind=job_type,
            )
            if owner_user_id is None:
                return

            match_res = self.db.table("matches").select(
                "id,status,fit_score,positive_reasons,missing_information"
            ).eq("search_id", search_id).eq("listing_id", listing_id).limit(1).execute()
            if not match_res.data:
                raise RuntimeError(f"Match not found for renter notification: {search_id}/{listing_id}")
            match = match_res.data[0]
            match_id = str(match["id"])
            
            user_res = self.db.table("users").select("telegram_user_id").eq("id", owner_user_id).execute()
            if not user_res.data:
                return
            
            telegram_user_id = user_res.data[0]['telegram_user_id']
            
            # Fetch listing details
            listing_res = self.db.table("listings").select("*").eq("id", listing_id).execute()
            if not listing_res.data:
                return
            listing = listing_res.data[0]
            
            message_text = self.build_match_card(1, match, listing)
            
            # Initialize bot just for sending message
            from aiogram import Bot
            from app.config import settings
            
            keyboard = _match_action_keyboard(match_id)

            # The search may have been paused, cancelled, or edited while the
            # queued job was loading its match. Recheck at the send boundary.
            current_owner_user_id = self._active_search_owner(
                search_id,
                expected_version=expected_version,
                notification_kind=job_type,
            )
            if current_owner_user_id != owner_user_id:
                return

            bot = Bot(token=settings.telegram_bot_token)
            
            import asyncio
            try:
                await bot.send_message(chat_id=telegram_user_id, text=message_text, reply_markup=keyboard, parse_mode="HTML")
            finally:
                await bot.session.close()
            
            logger.info(f"Sent Telegram notification to {telegram_user_id} for match {listing_id}")
            
        elif job_type == "PROPOSE_VISIT_TO_RENTER":
            visit_id = payload['visit_id']
            search_id = payload['search_id']
            expected_version = payload.get("search_version")

            owner_user_id = self._active_search_owner(
                search_id,
                expected_version=expected_version,
                notification_kind=job_type,
            )
            if owner_user_id is None:
                return

            user_res = self.db.table("users").select("telegram_user_id").eq("id", owner_user_id).execute()
            if not user_res.data:
                return

            telegram_user_id = user_res.data[0]['telegram_user_id']
            
            # Fetch visit details
            visit_result = self.db.table("visits").select("*").eq("id", visit_id).eq(
                "search_id", search_id
            ).limit(1).execute()
            if not visit_result.data:
                return
            visit = visit_result.data[0]
            if visit.get("status") != "AWAITING_RENTER_CONFIRMATION":
                return
            proposed_start = visit['proposed_start']
            
            message_text = f"📅 The landlord has proposed a visit time:\n{proposed_start}\n\nDoes this work for you?"
            
            from aiogram import Bot
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            from app.config import settings
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Confirm Visit", callback_data=f"visit_confirm_{visit_id}")],
                [InlineKeyboardButton(text="Decline / Propose New Time", callback_data=f"visit_decline_{visit_id}")]
            ])

            current_owner_user_id = self._active_search_owner(
                search_id,
                expected_version=expected_version,
                notification_kind=job_type,
            )
            if current_owner_user_id != owner_user_id:
                return

            bot = Bot(token=settings.telegram_bot_token)

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
                
        elif job_type in {"MATCH_ACTIVE_SEARCH", "SEARCH_CREATED", "SEARCH_UPDATED"}:
            search_id = payload['search_id']
            logger.info(f"Processing matching job for search {search_id}")
            self.matching_engine.process_new_search(search_id)
            
        else:
            logger.warning(f"Unknown job type: {job_type}")
