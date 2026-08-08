from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender
from aiogram.enums import ChatAction
from app.telegram.states import RenterState
from app.requirements.service import RequirementService
from app.db.client import get_supabase_client
from uuid import uuid4

router = Router()
req_service = RequirementService()

# Words that signal "I'm done, start searching"
DONE_PHRASES = {"no", "nope", "nothing", "that's it", "thats it", "start searching",
                "go ahead", "begin", "nah", "all good", "nothing else",
                "no thanks", "search", "lets go", "let's go", "begin searching"}

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

# ─── COMMANDS (registered FIRST so they aren't swallowed by FSM states) ───

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Hey there! I'm FlatHunter, your personal flat-finding assistant.\n\n"
        "Tell me what you're looking for — area, budget, type of place — "
        "and I'll start hunting for you!"
    )
    await state.set_state(RenterState.waiting_for_requirement)
    await state.update_data(chat_history=[])

@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == RenterState.waiting_for_requirement.state:
        help_text = (
            "🔍 <b>I'm waiting for your requirements!</b>\n\n"
            "Just tell me what you're looking for. For example:\n"
            "• <i>\"2BHK in Gachibowli under 30k\"</i>\n"
            "• <i>\"Private room near HITEC City, furnished\"</i>\n\n"
            "<b>Commands:</b>\n"
            "/start - Restart the search\n"
            "/help - Show this message"
        )
    elif current_state == RenterState.collecting_extras.state:
        help_text = (
            "✅ <b>I have your basic requirements!</b>\n\n"
            "You can add extra preferences like:\n"
            "• <i>\"Furnished with parking\"</i>\n"
            "• <i>\"No brokerage, near metro\"</i>\n\n"
            "Or say <b>\"start searching\"</b> to begin!\n\n"
            "<b>Commands:</b>\n"
            "/start - Restart the search\n"
            "/help - Show this message"
        )
    else:
        help_text = (
            "👋 <b>Welcome to FlatHunter!</b>\n\n"
            "I help you find the perfect flat. Here's what I can do:\n\n"
            "<b>Commands:</b>\n"
            "/mysearch - Check status & matches of your active search\n"
            "/start - Start or restart your flat search\n"
            "/set_availability - Set when you're free for visits\n"
            "/help - Show this message\n\n"
            "Just say <b>hi</b> to get started!"
        )
    await message.answer(help_text, parse_mode="HTML")

@router.message(Command("mysearch"))
async def cmd_mysearch(message: Message):
    db = get_supabase_client()
    tg_id = message.from_user.id
    
    # 1. Get user
    user_res = db.table("users").select("id").eq("telegram_user_id", tg_id).execute()
    if not user_res.data:
        await message.answer("You don't have an active search yet! Say <b>hi</b> or tap /start to begin looking for a flat.", parse_mode="HTML")
        return
        
    user_id = user_res.data[0]['id']
    
    # 2. Get active search sessions
    session_res = db.table("search_sessions").select("id, status, created_at").eq("user_id", user_id).order("created_at", desc=True).limit(1).execute()
    if not session_res.data:
        await message.answer("You don't have any active searches right now. Say <b>hi</b> or tap /start to start a search!", parse_mode="HTML")
        return
        
    search_session = session_res.data[0]
    search_id = search_session['id']
    
    # 3. Get requirements
    req_res = db.table("search_requirements").select("*").eq("search_id", search_id).execute()
    req = req_res.data[0] if req_res.data else {}
    
    # 4. Get matches summary
    matches_res = db.table("matches").select("id, status, fit_score").eq("search_id", search_id).execute()
    total_matches = len(matches_res.data)
    strong_matches = len([m for m in matches_res.data if m.get("status") == "STRONG_MATCH"])
    qualifying_matches = len([m for m in matches_res.data if m.get("status") == "NEEDS_QUALIFICATION"])
    
    locations = ", ".join(req.get("preferred_locations", [])) or "Anywhere in Hyderabad"
    types = ", ".join(req.get("listing_types", [])) or "Any"
    budget = f"Up to ₹{req.get('max_rent'):,}" if req.get("max_rent") and req.get("max_rent") < 999999 else "Not specified"
    
    status_icon = "🟢 ACTIVE" if search_session.get("status") == "ACTIVE" else f"⚪ {search_session.get('status')}"
    
    summary = (
        f"<b>📋 Your Rental Search Status</b>\n\n"
        f"<b>Status:</b> {status_icon}\n"
        f"<b>Locations:</b> {locations}\n"
        f"<b>Property Type:</b> {types}\n"
        f"<b>Budget:</b> {budget}\n\n"
        f"<b>📊 Progress:</b>\n"
        f"• 🎯 Direct Strong Matches: <b>{strong_matches}</b>\n"
        f"• ⏳ Properties In Qualification: <b>{qualifying_matches}</b>\n\n"
        f"<i>I'm continuously scanning new listings. I will message you the moment a flat matches your criteria!</i>\n\n"
        f"Want to update your search? Tap /start to restart."
    )
    await message.answer(summary, parse_mode="HTML")

@router.message(Command("set_availability"))
async def cmd_set_availability(message: Message, state: FSMContext):
    await message.answer("When are you generally free to visit properties? (e.g., 'Weekends anytime, weekdays after 6 PM')")
    await state.set_state(RenterState.waiting_for_availability)

# ─── GREETING CATCH (after commands, before FSM states) ───

@router.message(F.text.lower().in_({"hi", "hello", "hey", "get started", "hey bot"}))
async def greeting_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Hey there! I'm FlatHunter, your personal flat-finding assistant.\n\n"
        "Tell me what you're looking for — area, budget, type of place — "
        "and I'll start hunting for you!"
    )
    await state.set_state(RenterState.waiting_for_requirement)
    await state.update_data(chat_history=[])

# ─── FSM STATE HANDLERS ───

@router.message(RenterState.waiting_for_requirement)
async def process_requirement(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_history = data.get("chat_history", [])
    
    # Show typing immediately so user knows bot is alive
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    chat_history.append(f"User: {message.text}")
    full_conversation = "\n".join(chat_history)
    
    try:
        user_id = await get_or_create_user(message)
        
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            parsed_reqs = await req_service.parse_requirements(full_conversation)
        
        if not parsed_reqs.is_complete:
            bot_reply = parsed_reqs.follow_up_question or "Could you tell me a bit more about the area and budget you have in mind?"
            await message.answer(bot_reply)
            
            chat_history.append(f"Bot: {bot_reply}")
            await state.update_data(chat_history=chat_history)
            return
            
        summary = parsed_reqs.conversational_summary or "Got it! I have all the details I need."
        await message.answer(
            f"{summary}\n\n"
            "Anything else you'd like me to keep in mind? "
            "(e.g. furnished, no brokerage, near metro, parking)\n\n"
            "Or just say <b>\"start searching\"</b> and I'll begin right away! 🚀",
            parse_mode="HTML"
        )
        
        await state.update_data(
            chat_history=chat_history,
            parsed_reqs=parsed_reqs.model_dump(),
            user_id=user_id,
            full_conversation=full_conversation
        )
        await state.set_state(RenterState.collecting_extras)
        
    except Exception as e:
        await message.answer(f"Sorry, I had trouble understanding that. Could you try rephrasing? ({str(e)})")

@router.message(RenterState.collecting_extras)
async def process_extras(message: Message, state: FSMContext):
    data = await state.get_data()
    user_text = message.text.strip().lower()
    
    chat_history = data.get("chat_history", [])
    user_id = data.get("user_id")
    
    if user_text in DONE_PHRASES:
        from app.requirements.schemas import RequirementExtractionResponse
        parsed_reqs = RequirementExtractionResponse(**data["parsed_reqs"])
        
        if parsed_reqs.max_rent is None:
            parsed_reqs.max_rent = 999999
            
        full_conversation = data.get("full_conversation", "\n".join(chat_history))
        session = req_service.create_search(user_id, parsed_reqs, full_conversation)
        
        await message.answer(
            "🚀 <b>Your search is live!</b>\n\n"
            "I'll message you right here as soon as I find a matching property. "
            "Sit back and relax — I'm on it!",
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    # Show typing immediately
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    chat_history.append(f"User: {message.text}")
    full_conversation = "\n".join(chat_history)
    
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            parsed_reqs = await req_service.parse_requirements(full_conversation)
        
        await message.answer(
            "✅ Noted! I'll factor that into your search.\n\n"
            "Anything else? Or say <b>\"start searching\"</b> to begin! 🚀",
            parse_mode="HTML"
        )
        
        chat_history.append(f"Bot: Noted! I'll factor that into your search.")
        await state.update_data(
            chat_history=chat_history,
            parsed_reqs=parsed_reqs.model_dump(),
            full_conversation=full_conversation
        )
        
    except Exception as e:
        await message.answer(f"I noted that, but had a small hiccup: {str(e)}. Let's continue — anything else?")

@router.message(RenterState.waiting_for_availability)
async def process_availability(message: Message, state: FSMContext):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    user_id = await get_or_create_user(message)
    
    from app.scheduling.service import SchedulingService
    sched_service = SchedulingService()
    
    try:
        await sched_service.parse_and_save_availability(user_id, None, message.text)
        await message.answer("✅ Availability saved! I'll use this when landlords propose times.")
    except Exception as e:
        await message.answer(f"Failed to save availability: {e}")
    finally:
        await state.clear()

# ─── CALLBACK QUERIES ───

@router.callback_query(F.data.startswith("contact_"))
async def process_contact_callback(callback: CallbackQuery):
    from app.qualification.service import QualificationService
    
    data_parts = callback.data.split("_")
    search_id = data_parts[1]
    listing_id = data_parts[2]
    
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
    
    outreach_msg = await qual_service.generate_initial_outreach(conv_id)
    await callback.message.answer(f"Drafted and sent outreach: \n\n{outreach_msg}")
    
    await callback.answer()

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

# ─── FALLBACK (must be LAST) ───

@router.message()
async def renter_fallback(message: Message):
    await message.answer(
        "I didn't quite catch that. Type /help to see what I can do, "
        "or say <b>hi</b> to start searching for a flat!",
        parse_mode="HTML"
    )
