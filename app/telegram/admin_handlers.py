import json
import re

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender
from app.config import settings
from app.telegram.states import AdminState, RenterState
from app.ingestion.service import DraftApprovalError, IngestionService
from app.telegram.renter_handlers import get_or_create_user

router = Router()
ingest_service = IngestionService()

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids


async def _load_information_images(message: Message, session_id: str) -> dict[str, bytes]:
    """Download screenshots transiently for extraction; do not persist their bytes."""
    from io import BytesIO
    from app.db.client import get_supabase_client
    inputs = get_supabase_client().table("ingestion_inputs").select("id,telegram_file_id").eq(
        "ingestion_session_id", str(session_id)
    ).eq("input_type", "IMAGE").eq("is_information_bearing", True).execute()
    result: dict[str, bytes] = {}
    for item in inputs.data or []:
        if item.get("telegram_file_id"):
            file_info = await message.bot.get_file(item["telegram_file_id"])
            buffer = BytesIO()
            await message.bot.download_file(file_info.file_path, destination=buffer)
            result[str(item["id"])] = buffer.getvalue()
    return result


def _draft_review_sections(draft: dict) -> list[tuple[str, object]]:
    """Expose every useful stored draft field during admin review."""
    sections: list[tuple[str, object]] = [("Canonical fields", draft.get("canonical_payload") or {})]
    context = draft.get("extracted_context") or {}
    if context:
        sections.append(("Additional extracted information", context))
    conflicts = draft.get("conflicts") or []
    if conflicts:
        sections.append(("Conflicts requiring review", conflicts))
    draft_details = {
        field: draft.get(field)
        for field in ("content_type", "extraction_status", "group_key", "created_at", "updated_at")
        if draft.get(field) is not None
    }
    if draft_details:
        sections.append(("Draft details", draft_details))
    metadata = draft.get("model_metadata") or {}
    if metadata:
        sections.append(("Extraction metadata", metadata))
    return sections


def _draft_action_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View full details / Edit", callback_data=f"review_draft_{draft_id}")],
        [
            InlineKeyboardButton(text="Approve", callback_data=f"approve_draft_{draft_id}"),
            InlineKeyboardButton(text="Reject", callback_data=f"reject_draft_{draft_id}"),
        ],
    ])


def _json_chunks(value: object, max_chars: int = 3500) -> list[str]:
    rendered = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return [rendered[index:index + max_chars] for index in range(0, len(rendered), max_chars)] or ["{}"]


def _parse_draft_edits(command_text: str) -> dict[str, object]:
    body = command_text.partition(" ")[2].strip()
    if not body:
        raise ValueError("Use /editdraft field=value; field=value")
    edits: dict[str, object] = {}
    money_fields = {"rent", "maintenance", "deposit", "brokerage"}
    boolean_fields = {"attached_bathroom", "car_parking", "bike_parking"}
    enum_fields = {"listing_type", "furnishing"}
    for assignment in body.split(";"):
        if "=" not in assignment:
            raise ValueError(f"Invalid edit {assignment.strip()!r}; expected field=value")
        field, raw_value = (part.strip() for part in assignment.split("=", 1))
        field = field.lower()
        if not field or not raw_value:
            raise ValueError("Every draft edit needs both a field and a value")
        if raw_value.lower() == "null":
            value: object = None
        elif field in money_fields:
            digits = re.sub(r"[^0-9-]", "", raw_value)
            if not digits:
                raise ValueError(f"{field} must be a number or null")
            value = int(digits)
        elif field in boolean_fields:
            normalized = raw_value.lower()
            if normalized not in {"true", "false", "yes", "no"}:
                raise ValueError(f"{field} must be true, false, yes, no, or null")
            value = normalized in {"true", "yes"}
        elif field in enum_fields:
            value = raw_value.strip().upper().replace("-", "_").replace(" ", "_")
        else:
            value = raw_value
        edits[field] = value
    return edits


async def _send_draft_preview(message: Message, draft_id: str) -> None:
    from app.db.client import get_supabase_client
    result = get_supabase_client().table("listing_drafts").select("*").eq("id", str(draft_id)).execute()
    if not result.data:
        await message.answer("Draft could not be loaded.")
        return
    draft = result.data[0]
    await message.answer(f"Draft ready for review (ID: {draft_id}, type: {draft.get('content_type', 'UNKNOWN')}).")
    for title, content in _draft_review_sections(draft):
        for index, chunk in enumerate(_json_chunks(content), start=1):
            suffix = f" ({index})" if index > 1 else ""
            await message.answer(f"{title}{suffix}:\n{chunk}", parse_mode=None)

    canonical = draft.get("canonical_payload") or {}
    missing = []
    if canonical.get("rent") is None:
        missing.append("rent")
    if not canonical.get("locality"):
        missing.append("locality")
    if canonical.get("listing_type") not in {"ENTIRE_PROPERTY", "PRIVATE_ROOM", "SHARED_ROOM"}:
        missing.append("listing_type")
    correction = ""
    if missing:
        correction = (
            f"\nMissing approval fields: {', '.join(missing)}. "
            "Correct them with, for example:\n/editdraft rent=25000; locality=Manikonda"
        )
    await message.answer(
        "Editable fields: listing_type, property_configuration, city, locality, location_text, landmark, "
        "rent, maintenance, deposit, brokerage, available_from, furnishing, attached_bathroom, "
        "car_parking, bike_parking.\n"
        "Send /editdraft field=value; field=value to make corrections, /approve to publish, "
        f"or /cancel to close this review without changing the draft.{correction}"
    )
@router.message(Command("renter"), lambda msg: is_admin(msg.from_user.id))
async def cmd_renter_mode(message: Message, state: FSMContext):
    """Let an authorized admin exercise the renter flow with the same Telegram account."""
    await get_or_create_user(message)
    await state.clear()
    await state.set_state(RenterState.waiting_for_requirement)
    await state.update_data(chat_history=[])
    await message.answer(
        "Renter test mode is active for this chat. Tell me what flat you are looking for, "
        "or use /start. Use /admin when you want to return to admin actions."
    )


@router.message(Command("admin"), lambda msg: is_admin(msg.from_user.id))
async def cmd_admin_mode(message: Message, state: FSMContext):
    """Exit a renter/admin FSM flow without changing the user's persisted role."""
    await state.clear()
    await message.answer("Admin mode is active again. Use /help to view admin commands, or /renter to test the renter flow.")


@router.message(Command("help"), StateFilter(None), lambda msg: is_admin(msg.from_user.id))
async def cmd_help(message: Message):
    if not is_admin(message.from_user.id):
        return
        
    help_text = (
        "<b>Admin Commands:</b>\n"
        "/addlisting - Add a new property\n"
        "/bulkadd - Add multiple properties at once\n"
        "/status - View system metrics\n"
        "/viewsearches - View active renters' searches\n"
        "/viewlistings - View recent active listings\n"
        "/viewdrafts - Review, edit, approve, or reject pending drafts\n"
        "/sim_reply - Simulate an owner SMS reply\n"
        "/help - Show this message"
    )
    await message.answer(help_text, parse_mode="HTML")

@router.message(Command("status"), lambda msg: is_admin(msg.from_user.id))
async def cmd_status(message: Message):
    from app.db.client import get_supabase_client
    db = get_supabase_client()
    
    # Use exact count with select limit=1 for optimization
    listings = db.table("listings").select("id", count="exact").eq("availability_status", "AVAILABLE").limit(1).execute()
    renters = db.table("search_sessions").select("id", count="exact").eq("status", "ACTIVE").limit(1).execute()
    drafts = db.table("listing_drafts").select("id", count="exact").eq("extraction_status", "SUCCESS").limit(1).execute()
    
    msg = (
        "<b>📊 System Status:</b>\n"
        f"🏠 Active Listings: {listings.count}\n"
        f"📝 Drafts in DB: {drafts.count}\n"
        f"🔍 Active Renters: {renters.count}"
    )
    await message.answer(msg, parse_mode="HTML")

@router.message(Command("viewsearches"), lambda msg: is_admin(msg.from_user.id))
async def cmd_view_searches(message: Message):
    from app.db.client import get_supabase_client
    db = get_supabase_client()
    
    # Join searches, users, and requirements
    res = db.table("search_sessions").select("id, created_at, users(display_name, telegram_username), search_requirements(*)").eq("status", "ACTIVE").order("created_at", desc=True).limit(5).execute()
    
    if not res.data:
        await message.answer("No active searches right now.")
        return
        
    lines = ["<b>🔍 Latest Active Searches:</b>\n"]
    for s in res.data:
        user = s.get("users") or {}
        user_name = user.get("display_name") or user.get("telegram_username") or "Unknown"
        reqs = s.get("search_requirements", [])
        req = reqs[0] if isinstance(reqs, list) and len(reqs) > 0 else (reqs if isinstance(reqs, dict) else {})
        
        locations = ", ".join(req.get("preferred_locations", [])) if req.get("preferred_locations") else "Any"
        budget = f"₹{req.get('max_rent'):,}" if req.get("max_rent") and req.get("max_rent") < 999999 else "Any"
        types_list = req.get("listing_types") or []
        config_list = req.get("preferred_property_configurations") or []
        combined_types = [t for t in types_list + config_list if t]
        types = ", ".join(combined_types) if combined_types else "Any"
        
        lines.append(f"👤 <b>{user_name}</b>")
        lines.append(f"Looking for: {types} in {locations}")
        lines.append(f"Budget: {budget}")
        lines.append(f"<i>Started: {s.get('created_at', '')[:10]}</i>\n")
        
    await message.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("viewlistings"), lambda msg: is_admin(msg.from_user.id))
async def cmd_view_listings(message: Message):
    from app.db.client import get_supabase_client
    db = get_supabase_client()
    
    res = db.table("listings").select("*").eq("availability_status", "AVAILABLE").order("created_at", desc=True).limit(5).execute()
    
    if not res.data:
        await message.answer("No active listings right now.")
        return
        
    await message.answer("<b>🏠 Latest Active Listings:</b>", parse_mode="HTML")
    for l in res.data:
        listing_id = l.get('id')
        config = l.get("property_configuration") or l.get("listing_type") or "Unknown"
        loc = l.get("locality") or l.get("location_text") or "Unknown"
        rent = f"₹{l.get('rent'):,}" if l.get("rent") else "Unknown"
        
        text = f"🔹 <b>{config} in {loc}</b>\nRent: {rent}\nID: <code>{listing_id}</code>"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏸️ Deactivate", callback_data=f"deactivate_listing_{listing_id}")]
        ])
        
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@router.message(Command("viewdrafts"), lambda msg: is_admin(msg.from_user.id))
async def cmd_view_drafts(message: Message):
    from app.db.client import get_supabase_client
    db = get_supabase_client()
    
    res = db.table("listing_drafts").select("*").eq("extraction_status", "SUCCESS").order("created_at", desc=True).limit(5).execute()
    
    if not res.data:
        await message.answer("No drafts right now.")
        return
        
    await message.answer("<b>📝 Latest Drafts:</b>", parse_mode="HTML")
    for d in res.data:
        draft_id = d.get('id')
        status = d.get('extraction_status', 'UNKNOWN')
        payload = d.get('canonical_payload', {})
        
        config = payload.get('property_configuration') or payload.get('listing_type', 'Property')
        loc = payload.get('locality') or payload.get('city') or 'Unknown'
        rent = f"₹{payload.get('rent'):,}" if payload.get('rent') else "Unknown"
        
        text = f"🔹 <b>{config} in {loc}</b>\nRent: {rent}\nStatus: {status}\nID: <code>{draft_id}</code>"
        
        keyboard = _draft_action_keyboard(str(draft_id))
        
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@router.message(Command("addlisting"), lambda msg: is_admin(msg.from_user.id))
async def cmd_add_listing(message: Message, state: FSMContext):
    user_id = await get_or_create_user(message)
    session_id = ingest_service.create_session(user_id, "SINGLE")
    
    await state.update_data(session_id=session_id)
    await state.set_state(AdminState.waiting_for_listing_info)
    await message.answer("Started ingestion session. Send me all details, screenshots, and text. When finished, send /doneinfo.")

@router.message(AdminState.waiting_for_listing_info, Command("doneinfo"))
async def cmd_done_info(message: Message, state: FSMContext):
    session_id = (await state.get_data()).get("session_id")
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            image_bytes = await _load_information_images(message, session_id)
            draft_id = await ingest_service.complete_session_and_extract(session_id, image_bytes)
        await state.update_data(draft_id=draft_id)
        await state.set_state(AdminState.waiting_for_listing_media)
        await message.answer("Information is extracted. Send optional property photos now; they will not influence extraction. Send /donephotos when finished.")
    except Exception as error:
        await message.answer(f"Failed to extract: {error}")
        await state.clear()
@router.message(AdminState.waiting_for_listing_info, F.text & ~F.text.startswith("/"))
async def process_listing_text(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")
    ingest_service.add_text_input(session_id, message.text)
    await message.answer("Saved text. Send more info, screenshots, or /doneinfo.")

@router.message(AdminState.waiting_for_listing_info, F.photo)
async def process_listing_information_photo(message: Message, state: FSMContext):
    session_id = (await state.get_data()).get("session_id")
    photo = message.photo[-1]
    ingest_service.add_image_input(session_id, photo.file_id, photo.file_unique_id, is_information_bearing=True, caption=message.caption)
    await message.answer("Saved information screenshot. Send more information or /doneinfo.")


@router.message(AdminState.waiting_for_listing_media, F.photo)
async def process_listing_media_photo(message: Message, state: FSMContext):
    session_id = (await state.get_data()).get("session_id")
    photo = message.photo[-1]
    ingest_service.add_image_input(session_id, photo.file_id, photo.file_unique_id, is_information_bearing=False, caption=message.caption)
    await message.answer("Saved property photo. Send more photos or /donephotos.")


@router.message(AdminState.waiting_for_listing_media, Command("donephotos"))
async def cmd_done_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    ingest_service.complete_media_stage(data["session_id"])
    await state.set_state(AdminState.confirming_listing)
    await _send_draft_preview(message, data["draft_id"])


@router.message(AdminState.waiting_for_listing_media, F.text)
async def process_listing_media_text(message: Message):
    await message.answer("Please send property photos, or /donephotos to continue to approval.")
@router.message(AdminState.confirming_listing, Command("approve"))
async def cmd_approve_draft(message: Message, state: FSMContext):
    data = await state.get_data()
    draft_id = data.get("draft_id")
    
    try:
        listing_id = ingest_service.approve_draft(draft_id)
    except DraftApprovalError as error:
        await message.answer(
            f"This draft cannot be approved yet. {error}. "
            "Use /editdraft field=value; field=value, then try /approve again."
        )
        return
    except ValueError as error:
        await message.answer(f"Could not approve this draft: {error}")
        return
    await message.answer(f"Listing approved and created! (Listing ID: {listing_id})")
    await state.clear()


@router.message(AdminState.confirming_listing, Command("editdraft"))
async def cmd_edit_draft(message: Message, state: FSMContext):
    draft_id = (await state.get_data()).get("draft_id")
    try:
        edits = _parse_draft_edits(message.text or "")
        ingest_service.update_draft_canonical(draft_id, edits)
    except (ValueError, RuntimeError) as error:
        await message.answer(f"Draft edit failed: {error}")
        return
    await message.answer("Draft updated and revalidated.")
    await _send_draft_preview(message, draft_id)

@router.message(AdminState.confirming_listing, Command("cancel"))
async def cmd_cancel_draft(message: Message, state: FSMContext):
    await message.answer("Draft review closed. The draft remains pending in /viewdrafts.")
    await state.clear()

@router.message(Command("bulkadd"), lambda msg: is_admin(msg.from_user.id))
async def cmd_bulk_add(message: Message, state: FSMContext):
    user_id = await get_or_create_user(message)
    session_id = ingest_service.create_session(user_id, "BULK")
    
    await state.update_data(session_id=session_id)
    await state.set_state(AdminState.waiting_for_listing_info)
    await message.answer("Started BULK ingestion session. Send me multiple listings separated by --- or send multiple images. When finished, send /donebulk.")

@router.message(AdminState.waiting_for_listing_info, Command("donebulk"))
async def cmd_done_bulk(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")
    
    await message.answer("Extracting multiple listings in bulk...")
    try:
        image_bytes = await _load_information_images(message, session_id)
        draft_ids = await ingest_service.complete_bulk_session_and_extract(session_id, image_bytes)
        await state.clear()
        await message.answer(f"Extracted {len(draft_ids)} independent drafts. Use /viewdrafts to approve or reject each one.")
    except Exception as e:
        await message.answer(f"Failed to extract bulk: {e}")
        await state.clear()

from app.qualification.service import QualificationService

@router.message(Command("sim_reply"), lambda msg: is_admin(msg.from_user.id))
async def cmd_sim_reply(message: Message):
        
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /sim_reply <conv_id> <reply text>")
        return
        
    conv_id = parts[1]
    reply_text = parts[2]
    
    qual_service = QualificationService()
    await message.answer("Processing landlord reply...")
    try:
        await qual_service.process_inbound_reply(conv_id, reply_text)
        await message.answer("Reply processed. Check logs or DB for next question/state updates.")
    except Exception as e:
        await message.answer(f"Error processing reply: {e}")


@router.callback_query(F.data.startswith("review_draft_"), lambda call: is_admin(call.from_user.id))
async def process_review_draft_callback(callback: CallbackQuery, state: FSMContext):
    if callback.message is None:
        await callback.answer("This draft cannot be opened from this message.", show_alert=True)
        return

    draft_id = callback.data.removeprefix("review_draft_")
    await state.clear()
    await state.update_data(draft_id=draft_id)
    await state.set_state(AdminState.confirming_listing)
    await callback.answer()
    await _send_draft_preview(callback.message, draft_id)

@router.callback_query(F.data.startswith("approve_draft_"), lambda call: is_admin(call.from_user.id))
async def process_approve_draft_callback(callback: CallbackQuery):
    draft_id = callback.data.replace("approve_draft_", "")
    try:
        listing_id = ingest_service.approve_draft(draft_id)
        await callback.message.edit_text(callback.message.html_text + f"\n\n✅ <b>Approved!</b>\nListing ID: <code>{listing_id}</code>", parse_mode="HTML", reply_markup=None)
    except Exception as e:
        await callback.answer(f"Failed to approve: {e}", show_alert=True)
    await callback.answer()

@router.callback_query(F.data.startswith("reject_draft_"), lambda call: is_admin(call.from_user.id))
async def process_reject_draft_callback(callback: CallbackQuery):
    draft_id = callback.data.replace("reject_draft_", "")
    from app.db.client import get_supabase_client
    db = get_supabase_client()
    try:
        ingest_service.reject_draft(draft_id)
        await callback.message.edit_text(callback.message.html_text + "\n\n❌ <b>Rejected.</b>", parse_mode="HTML", reply_markup=None)
    except Exception as e:
        await callback.answer(f"Failed to reject: {e}", show_alert=True)
    await callback.answer()

@router.callback_query(F.data.startswith("deactivate_listing_"), lambda call: is_admin(call.from_user.id))
async def process_deactivate_listing_callback(callback: CallbackQuery):
    listing_id = callback.data.replace("deactivate_listing_", "")
    from app.db.client import get_supabase_client
    db = get_supabase_client()
    try:
        db.table("listings").update({"availability_status": "UNAVAILABLE"}).eq("id", listing_id).execute()
        await callback.message.edit_text(callback.message.html_text + "\n\n⏸️ <b>Deactivated.</b>", parse_mode="HTML", reply_markup=None)
    except Exception as e:
        await callback.answer(f"Failed to deactivate: {e}", show_alert=True)
    await callback.answer()

@router.message(StateFilter(None), lambda msg: is_admin(msg.from_user.id))
async def admin_fallback(message: Message):
    await message.answer("I didn't quite catch that. Type /help to see a list of available commands.")
