from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from app.config import settings
from app.telegram.states import AdminState
from app.ingestion.service import IngestionService
from app.telegram.renter_handlers import get_or_create_user

router = Router()
ingest_service = IngestionService()

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids

@router.message(Command("addlisting"))
async def cmd_add_listing(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    user_id = await get_or_create_user(message)
    session_id = ingest_service.create_session(user_id, "SINGLE")
    
    await state.update_data(session_id=session_id)
    await state.set_state(AdminState.waiting_for_listing_info)
    await message.answer("Started ingestion session. Send me all details, screenshots, and text. When finished, send /doneinfo.")

@router.message(AdminState.waiting_for_listing_info, Command("doneinfo"))
async def cmd_done_info(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")
    
    await message.answer("Extracting listing information...")
    try:
        draft_id = await ingest_service.complete_session_and_extract(session_id)
        await state.update_data(draft_id=draft_id)
        await state.set_state(AdminState.confirming_listing)
        await message.answer(f"Extracted draft successfully (ID: {draft_id}).\nSend /approve to publish or /cancel to abort.")
    except Exception as e:
        await message.answer(f"Failed to extract: {e}")
        await state.clear()

@router.message(AdminState.waiting_for_listing_info, F.text)
async def process_listing_text(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")
    ingest_service.add_text_input(session_id, message.text)
    await message.answer("Saved text. Send more info or /doneinfo.")

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

@router.message(Command("bulkadd"))
async def cmd_bulk_add(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
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

@router.message(Command("sim_reply"))
async def cmd_sim_reply(message: Message):
    if not is_admin(message.from_user.id):
        return
        
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
