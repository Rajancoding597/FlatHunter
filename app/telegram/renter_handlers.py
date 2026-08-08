from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from app.telegram.states import RenterState
from app.requirements.service import RequirementService
from app.db.client import get_supabase_client
from uuid import uuid4

router = Router()
req_service = RequirementService()

async def get_or_create_user(message: Message) -> str:
    db = get_supabase_client()
    tg_id = message.from_user.id
    res = db.table("users").select("*").eq("telegram_user_id", tg_id).execute()
    if res.data:
        return res.data[0]['id']
    
    new_user = db.table("users").insert({
        "telegram_user_id": tg_id,
        "telegram_username": message.from_user.username,
        "display_name": message.from_user.full_name,
        "role": "RENTER"
    }).execute()
    return new_user.data[0]['id']

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("Hello! I am FlatHunter, your rental search concierge.\n\nPlease describe what kind of flat you are looking for (e.g., 'Private room in Gachibowli under 25k, attached bath required').")
    await state.set_state(RenterState.waiting_for_requirement)

@router.message(RenterState.waiting_for_requirement)
async def process_requirement(message: Message, state: FSMContext):
    await message.answer("Let me analyze your requirements...")
    
    try:
        user_id = await get_or_create_user(message)
        parsed_reqs = await req_service.parse_requirements(message.text)
        
        # Save to DB
        session = req_service.create_search(user_id, parsed_reqs, message.text)
        
        summary = (
            f"✅ Search Created!\n"
            f"Types: {', '.join(parsed_reqs.listing_types)}\n"
            f"Locations: {', '.join(parsed_reqs.preferred_locations)}\n"
            f"Budget: Up to {parsed_reqs.max_rent}\n\n"
            f"I will start looking for matches."
        )
        await message.answer(summary)
        await state.clear()
        
    except Exception as e:
        await message.answer(f"Sorry, I had trouble understanding that. Please try again. ({str(e)})")

from aiogram.types import CallbackQuery
from app.qualification.service import QualificationService

@router.callback_query(F.data.startswith("contact_"))
async def process_contact_callback(callback: CallbackQuery):
    data_parts = callback.data.split("_")
    search_id = data_parts[1]
    listing_id = data_parts[2]
    
    # We need a contact_id, we just get the first one for the listing
    db = get_supabase_client()
    contacts_res = db.table("contacts").select("id").eq("listing_id", listing_id).execute()
    
    if not contacts_res.data:
        await callback.message.answer("This property doesn't have any contacts listed.")
        await callback.answer()
        return
        
    contact_id = contacts_res.data[0]['id']
    
    qual_service = QualificationService()
    conv_id = qual_service.start_conversation(search_id, listing_id, contact_id)
    
    await callback.message.answer(f"Started qualification conversation! ID: {conv_id}. I will reach out to them now.")
    
    # Kick off generation in background (or inline for now)
    outreach_msg = await qual_service.generate_initial_outreach(conv_id)
    await callback.message.answer(f"Drafted and sent outreach: \n\n{outreach_msg}")
    
    await callback.answer()

from aiogram.filters import Command

@router.message(Command("set_availability"))
async def cmd_set_availability(message: Message, state: FSMContext):
    await message.answer("When are you generally free to visit properties? (e.g., 'Weekends anytime, weekdays after 6 PM')")
    await state.set_state(RenterState.waiting_for_availability)

@router.message(RenterState.waiting_for_availability)
async def process_availability(message: Message, state: FSMContext):
    await message.answer("Parsing your availability...")
    user_id = await get_or_create_user(message)
    
    from app.scheduling.service import SchedulingService
    sched_service = SchedulingService()
    
    # We don't have the active search_id easily here, but we can pass None for general
    try:
        await sched_service.parse_and_save_availability(user_id, None, message.text)
        await message.answer("✅ Availability saved! I'll use this when landlords propose times.")
    except Exception as e:
        await message.answer(f"Failed to save availability: {e}")
    finally:
        await state.clear()

@router.callback_query(F.data.startswith("visit_confirm_"))
async def process_visit_confirm(callback: CallbackQuery):
    visit_id = callback.data.split("_")[2]
    
    from app.scheduling.service import SchedulingService
    sched = SchedulingService()
    sched.confirm_visit(visit_id)
    
    db = get_supabase_client()
    db.table("agent_jobs").insert({
        "job_type": "EMAIL_CONFIRM_VISIT",
        "status": "PENDING",
        "payload": {"visit_id": visit_id},
        "run_after": "now()"
    }).execute()
    
    await callback.message.edit_text("✅ Visit Confirmed! I've let the landlord know.")
    await callback.answer()

@router.callback_query(F.data.startswith("visit_decline_"))
async def process_visit_decline(callback: CallbackQuery):
    visit_id = callback.data.split("_")[2]
    
    from app.scheduling.service import SchedulingService
    sched = SchedulingService()
    sched.cancel_visit(visit_id)
    
    await callback.message.edit_text("❌ Visit Declined. We will continue the search.")
    await callback.answer()
