from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender
from app.config import settings
from app.telegram.states import AdminState
from app.ingestion.service import IngestionService
from app.telegram.renter_handlers import get_or_create_user

router = Router()
ingest_service = IngestionService()

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids

@router.message(Command("help"), lambda msg: is_admin(msg.from_user.id))
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
        "/viewdrafts - View pending drafts\n"
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
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_draft_{draft_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_draft_{draft_id}")
            ]
        ])
        
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
    data = await state.get_data()
    session_id = data.get("session_id")
    
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            draft_id = await ingest_service.complete_session_and_extract(session_id)
        
        await state.update_data(draft_id=draft_id)
        await state.set_state(AdminState.confirming_listing)
        
        from app.db.client import get_supabase_client
        import json
        db = get_supabase_client()
        draft = db.table("listing_drafts").select("canonical_payload").eq("id", str(draft_id)).execute().data[0]
        preview = json.dumps(draft["canonical_payload"], indent=2)
        
        await message.answer(f"Extracted draft successfully (ID: {draft_id}).\n\n<b>Preview:</b>\n<pre><code class='language-json'>{preview}</code></pre>\n\nSend /approve to publish or /cancel to abort.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Failed to extract: {e}")
        await state.clear()

@router.message(AdminState.waiting_for_listing_info, F.text)
async def process_listing_text(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")
    ingest_service.add_text_input(session_id, message.text)
    await message.answer("Saved text. Send more info, screenshots, or /doneinfo.")

@router.message(AdminState.waiting_for_listing_info, F.photo)
async def process_listing_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")
    
    import base64
    from io import BytesIO
    
    photo = message.photo[-1]
    file_info = await message.bot.get_file(photo.file_id)
    downloaded_file = await message.bot.download_file(file_info.file_path, destination=BytesIO())
    
    b64_data = base64.b64encode(downloaded_file.read()).decode("utf-8")
    ingest_service.add_image_input(session_id, b64_data)
    
    if message.caption:
        ingest_service.add_text_input(session_id, message.caption)
        
    await message.answer("Saved photo. Send more info, screenshots, or /doneinfo.")

@router.message(AdminState.confirming_listing, Command("approve"))
async def cmd_approve_draft(message: Message, state: FSMContext):
    data = await state.get_data()
    draft_id = data.get("draft_id")
    
    listing_id = ingest_service.approve_draft(draft_id)
    await message.answer(f"Listing approved and created! (Listing ID: {listing_id})")
    await state.clear()

@router.message(AdminState.confirming_listing, Command("cancel"))
async def cmd_cancel_draft(message: Message, state: FSMContext):
    await message.answer("Draft discarded.")
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
        draft_ids = await ingest_service.complete_bulk_session_and_extract(session_id)
        await state.clear()
        await message.answer(f"Extracted {len(draft_ids)} drafts successfully.\n(Approval queue logic to be added)")
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
        db.table("listing_drafts").update({"extraction_status": "REJECTED"}).eq("id", draft_id).execute()
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

@router.message(lambda msg: is_admin(msg.from_user.id))
async def admin_fallback(message: Message):
    await message.answer("I didn't quite catch that. Type /help to see a list of available commands.")
