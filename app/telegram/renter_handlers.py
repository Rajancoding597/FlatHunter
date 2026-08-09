from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender
from aiogram.enums import ChatAction
from app.telegram.states import RenterState
from app.requirements.service import RequirementService
from app.db.client import get_supabase_client
from app.common.tracer import tracer
from uuid import UUID
from datetime import datetime, timezone
from html import escape
import logging
from typing import Optional

from app.requirements.presentation import (
    format_requirement_diff,
    format_requirements,
    missing_core_fields,
)
from app.requirements.schemas import RequirementEditPlan
from app.telegram.command_menus import is_admin_menu_active
from app.telegram.renter_conversation import (
    PendingRenterAction,
    RenterConversationService,
    RenterIntent,
)

router = Router()
req_service = RequirementService()
conversation_service = RenterConversationService()
logger = logging.getLogger(__name__)

# Words that signal "I'm done, start searching"
DONE_PHRASES = {"no", "nope", "nothing", "that's it", "thats it", "start searching",
                "go ahead", "begin", "nah", "all good", "nothing else",
                "no thanks", "search", "lets go", "let's go", "begin searching"}

CONFIRM_ACTION_CALLBACK = 'renter:confirm'
DECLINE_ACTION_CALLBACK = 'renter:keep'


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Confirm', callback_data=CONFIRM_ACTION_CALLBACK),
        InlineKeyboardButton(text='Keep current', callback_data=DECLINE_ACTION_CALLBACK),
    ]])


async def _current_requirements(message: Message, state: FSMContext, telegram_user=None) -> tuple[dict, Optional[dict]]:
    data = await state.get_data()
    parsed = data.get('parsed_reqs')
    if parsed:
        return dict(parsed), None
    try:
        user_id = await get_or_create_user(message, telegram_user)
        session, requirements = req_service.get_editable_search(user_id)
        return requirements, session
    except ValueError:
        return {}, None


async def _classify_renter_turn(message: Message, state: FSMContext):
    data = await state.get_data()
    requirements, _ = await _current_requirements(message, state)
    return await conversation_service.classify(
        message.text or '',
        current_state=await state.get_state(),
        requirements=requirements,
        missing_fields=missing_core_fields(requirements),
        pending_action=data.get('pending_action'),
        recent_history=data.get('recent_turns') or [],
    )


async def _remember_turn(state: FSMContext, role: str, text: str) -> None:
    data = await state.get_data()
    history = list(data.get('recent_turns') or [])
    history.append({'role': role, 'text': text[:1000]})
    await state.update_data(recent_turns=history[-conversation_service.MAX_HISTORY_TURNS :])


async def _resume_flow(message: Message, state: FSMContext, requirements: Optional[dict] = None) -> None:
    prompt = conversation_service.resume_prompt(await state.get_state(), requirements)
    if prompt:
        await message.answer(prompt)


async def _show_current_requirements(message: Message, state: FSMContext, *, resume: bool = True) -> None:
    requirements, _ = await _current_requirements(message, state)
    await message.answer(format_requirements(requirements))
    if resume:
        await _resume_flow(message, state, requirements)


async def _set_pending_action(
    message: Message,
    state: FSMContext,
    pending: PendingRenterAction,
    prompt: str,
) -> None:
    await state.update_data(pending_action=pending.model_dump(mode='json'))
    await state.set_state(RenterState.confirming_conversational_action)
    await message.answer(prompt, reply_markup=_confirmation_keyboard())


async def _request_cancel_confirmation(message: Message, state: FSMContext) -> None:
    user_id = await get_or_create_user(message)
    try:
        session, _ = req_service.get_editable_search(user_id)
    except ValueError:
        await message.answer('You do not have an active or paused search to cancel.')
        return
    pending = PendingRenterAction(
        action='cancel_search',
        return_state=await state.get_state(),
        search_id=str(session.get('id')),
        search_version=int(session.get('version') or 1),
    )
    await _set_pending_action(
        message,
        state,
        pending,
        'Canceling stops all matching and alerts for this search. Do you want to cancel it?',
    )


async def _handle_search_edit(
    message: Message,
    state: FSMContext,
    text: str,
    *,
    show_after: bool = False,
) -> None:
    user_id = await get_or_create_user(message)
    try:
        session, current = req_service.get_editable_search(user_id)
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            plan = await req_service.parse_search_edit_plan(text, current)
        if not plan.changes:
            await message.answer('I could not identify a specific change. For example, say add Madhapur or set my maximum rent to 25k.')
            return
        proposed = req_service.apply_edit_plan(current, plan)
        if all(current.get(field) == proposed.get(field) for field in req_service.EDITABLE_FIELDS):
            await message.answer('That is already part of your saved search, so I left it unchanged.')
            if show_after:
                await message.answer(format_requirements(current, title='Your current requirements'))
            return
        missing = missing_core_fields(proposed)
        if missing:
            missing_text = ', '.join(missing)
            await message.answer(f'I cannot leave an active search without {escape(missing_text)}. Please give me a replacement value instead.')
            return
        if req_service.edit_plan_is_risky(current, plan):
            pending = PendingRenterAction(
                action='edit_search',
                return_state=await state.get_state(),
                search_id=str(session.get('id')),
                search_version=int(session.get('version') or 1),
                payload={'plan': plan.model_dump(mode='json'), 'show_after': show_after},
                raw_text=text,
            )
            confirmation_text = format_requirement_diff(current, proposed) + '\n\nDo you want me to save these changes?'
            await _set_pending_action(message, state, pending, confirmation_text)
            return
        version, updated = req_service.update_live_search_from_plan(
            user_id,
            UUID(str(session.get('id'))),
            plan,
            text,
            expected_version=int(session.get('version') or 1),
        )
    except (ValueError, RuntimeError) as error:
        await message.answer(escape(str(error)))
        return
    await message.answer(f'Saved that update. I am checking the inventory again using search version {version}.')
    if show_after:
        await message.answer(format_requirements(updated, title='Your updated requirements'))
    await state.clear()


async def _confirm_pending_action(message: Message, state: FSMContext, telegram_user=None) -> None:
    data = await state.get_data()
    raw_pending = data.get('pending_action')
    if not raw_pending:
        await message.answer('That confirmation has expired. Please send the request again.')
        await state.clear()
        return
    pending = PendingRenterAction(**raw_pending)
    user_id = await get_or_create_user(message, telegram_user)
    actor = telegram_user or message.from_user
    try:
        if pending.action == 'cancel_search':
            result = get_supabase_client().table('search_sessions').update({'status': 'CLOSED'}).eq('id', pending.search_id).eq('user_id', user_id).eq('version', pending.search_version).execute()
            if not result.data:
                raise RuntimeError('Your search changed elsewhere; please review it before canceling.')
            await message.answer('Your search is canceled. You will not receive further alerts for it.')
            tracer.log_event('SEARCH_CANCELLED', override_telegram_user_id=actor.id, payload={'search_id': pending.search_id})
            await state.clear()
            return
        if pending.action == 'replace_search':
            result = get_supabase_client().table('search_sessions').update({'status': 'CLOSED'}).eq('id', pending.search_id).eq('user_id', user_id).eq('version', pending.search_version).execute()
            if not result.data:
                raise RuntimeError('Your current search changed. Please use /start again.')
            await state.set_state(RenterState.waiting_for_requirement)
            await state.update_data(chat_history=[], parsed_reqs={}, pending_action=None, recent_turns=[])
            await message.answer('Your previous search is canceled. Tell me what you want in the new search.')
            return
        if pending.action == 'edit_search':
            plan = RequirementEditPlan(**pending.payload.get('plan', {}))
            version, updated = req_service.update_live_search_from_plan(
                user_id,
                UUID(str(pending.search_id)),
                plan,
                pending.raw_text or '',
                expected_version=pending.search_version,
            )
            await message.answer(f'Confirmed and saved. I am rematching your search using version {version}.')
            if pending.payload.get('show_after'):
                await message.answer(format_requirements(updated, title='Your updated requirements'))
            await state.clear()
            return
    except (ValueError, RuntimeError) as error:
        await message.answer(escape(str(error)))
        await state.clear()
        return
    await message.answer('That action is no longer available. Please send the request again.')
    await state.clear()


async def _decline_pending_action(message: Message, state: FSMContext, telegram_user=None) -> None:
    data = await state.get_data()
    raw_pending = data.get('pending_action')
    if not raw_pending:
        await message.answer('There is no pending change to keep or decline.')
        await state.clear()
        return
    pending = PendingRenterAction(**raw_pending)
    await state.update_data(pending_action=None)
    await state.set_state(pending.return_state)
    await message.answer('Kept your current search unchanged.')
    requirements, _ = await _current_requirements(message, state, telegram_user)
    await _resume_flow(message, state, requirements)


async def _activate_draft_from_state(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    search_id = data.get('search_id')
    user_id = data.get('user_id') or await get_or_create_user(message)
    if not search_id:
        await message.answer('Your draft is not ready yet. Tell me your requirements first.')
        return
    try:
        session = req_service.activate_search(user_id, search_id)
    except ValueError as error:
        await message.answer(f'I still need more information before starting: {escape(str(error))}')
        return
    await message.answer('Your search is live. I will message you when I find matching properties.')
    tracer.log_event('SEARCH_STARTED', override_telegram_user_id=message.from_user.id, payload={'search_id': str(session.id)}, override_search_id=str(session.id))
    await state.clear()


async def _show_matches(message: Message) -> None:
    user_id = await get_or_create_user(message)
    try:
        session, _ = req_service.get_editable_search(user_id)
    except ValueError:
        await message.answer('You do not have an active or paused search with matches yet.')
        return
    db = get_supabase_client()
    result = db.table('matches').select('*').eq('search_id', session.get('id')).order('fit_score', desc=True).limit(10).execute()
    matches = [item for item in (result.data or []) if item.get('status') != 'SKIPPED']
    matches.sort(key=lambda item: float(item.get('fit_score') or 0), reverse=True)
    matches = matches[:5]
    if not matches:
        await message.answer('I have not found a reviewable match yet. I am still checking new listings.')
        return
    from app.jobs.worker import JobWorker, _match_action_keyboard
    result_suffix = 's' if len(matches) != 1 else ''
    await message.answer(f'I found {len(matches)} result{result_suffix} to review.')
    for rank, match in enumerate(matches, start=1):
        listing_result = db.table('listings').select('*').eq('id', match.get('listing_id')).execute()
        if not listing_result.data:
            continue
        listing = listing_result.data[0]
        card = JobWorker.build_match_card(rank, match, listing)
        await message.answer(card, reply_markup=_match_action_keyboard(str(match.get('id'))))


async def _show_referenced_property(message: Message) -> None:
    user_id = await get_or_create_user(message)
    try:
        session, _ = req_service.get_editable_search(user_id)
    except ValueError:
        await message.answer('You do not have a current search result to describe.')
        return
    db = get_supabase_client()
    result = db.table('matches').select('*').eq('search_id', session.get('id')).order('fit_score', desc=True).limit(10).execute()
    matches = [item for item in (result.data or []) if item.get('status') != 'SKIPPED']
    if not matches:
        await message.answer('There is no current property match to describe yet.')
        return
    if len(matches) > 1:
        rows = [[InlineKeyboardButton(text=f'Property {index}', callback_data='details_match_' + str(item.get('id')))]
                for index, item in enumerate(matches[:5], start=1)]
        await message.answer('Which property do you mean?', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    match = matches[0]
    listing_result = db.table('listings').select('*').eq('id', match.get('listing_id')).execute()
    if not listing_result.data:
        await message.answer('That property is no longer available to review.')
        return
    from app.matching.details import clarification_labels, draft_property_narrative
    narrative = await draft_property_narrative(listing_result.data[0])
    clarifications = clarification_labels(match.get('missing_information'))
    suffix = ''
    if clarifications:
        suffix = '\n\nStill being confirmed with the owner: ' + ', '.join(clarifications) + '.'
    await message.answer(narrative + suffix, parse_mode=None)


async def _handle_conversational_decision(message: Message, state: FSMContext, decision) -> bool:
    '''Execute classified intents except requirement collection input.'''
    if is_admin_menu_active(message.chat.id):
        return True
    intents = list(decision.intents)
    lifecycle = {RenterIntent.PAUSE_SEARCH, RenterIntent.RESUME_SEARCH, RenterIntent.CANCEL_SEARCH}
    if len(lifecycle.intersection(intents)) > 1:
        await message.answer('Those search actions conflict. Should I pause, resume, or cancel the search?')
        return True

    handled = False
    resume_after = False
    skip_show = False
    for intent in intents:
        if intent == RenterIntent.REQUIREMENT_INPUT:
            continue
        if intent == RenterIntent.EDIT_REQUIREMENTS:
            await _handle_search_edit(
                message,
                state,
                decision.requirement_or_edit_text or message.text or '',
                show_after=RenterIntent.SHOW_REQUIREMENTS in intents,
            )
            handled = True
            skip_show = True
        elif intent == RenterIntent.SHOW_REQUIREMENTS and not skip_show:
            await _show_current_requirements(message, state, resume=False)
            handled = True
            resume_after = True
        elif intent == RenterIntent.SHOW_STATUS:
            await cmd_mysearch(message)
            handled = True
            resume_after = True
        elif intent == RenterIntent.SHOW_MATCHES:
            await _show_matches(message)
            handled = True
            resume_after = True
        elif intent == RenterIntent.PROPERTY_DETAILS:
            await _show_referenced_property(message)
            handled = True
            resume_after = True
        elif intent == RenterIntent.PAUSE_SEARCH:
            await cmd_pause_search(message)
            handled = True
            resume_after = True
        elif intent == RenterIntent.RESUME_SEARCH:
            await cmd_resume_search(message)
            handled = True
            resume_after = True
        elif intent == RenterIntent.CANCEL_SEARCH:
            await _request_cancel_confirmation(message, state)
            handled = True
        elif intent == RenterIntent.START_SEARCH:
            await _activate_draft_from_state(message, state)
            handled = True
        elif intent == RenterIntent.SET_AVAILABILITY:
            normalized = (message.text or '').casefold().strip()
            if normalized in {'set availability', 'update availability', 'change availability'}:
                await cmd_set_availability(message, state)
            else:
                from app.scheduling.service import SchedulingService
                try:
                    user_id = await get_or_create_user(message)
                    await SchedulingService().parse_and_save_availability(user_id, None, message.text or '')
                    await message.answer('Saved your visit availability.')
                    await state.clear()
                except Exception:
                    logger.exception('Could not save renter availability')
                    await message.answer('I could not save that availability. Try something like weekends anytime or weekdays after 6 PM.')
            handled = True
        elif intent == RenterIntent.RENTAL_QUESTION:
            answer = await conversation_service.answer_rental_question(decision.rental_question or message.text or '')
            await message.answer(answer, parse_mode=None)
            handled = True
            resume_after = True
        elif intent == RenterIntent.CONFIRM:
            await _confirm_pending_action(message, state)
            handled = True
        elif intent == RenterIntent.DECLINE:
            await _decline_pending_action(message, state)
            handled = True
        elif intent == RenterIntent.GREETING:
            current_state = await state.get_state()
            if current_state:
                await message.answer('Hi! I still have your progress, so we can continue where we left off.')
                resume_after = True
            else:
                await cmd_start(message, state)
            handled = True
        elif intent == RenterIntent.HELP:
            await cmd_help(message, state)
            handled = True
        elif intent == RenterIntent.OUT_OF_SCOPE:
            await message.answer('I can help with your flat search, saved requirements, matches, visits, and general rental questions. I cannot handle that unrelated request.')
            handled = True
            resume_after = True
        elif intent == RenterIntent.AMBIGUOUS:
            clarification = decision.clarification_question or 'I am not sure whether you want to change your search, view it, or ask a rental question. Could you clarify?'
            await message.answer(clarification[:1000], parse_mode=None)
            handled = True

    if resume_after and await state.get_state() != RenterState.confirming_conversational_action.state:
        requirements, _ = await _current_requirements(message, state)
        await _resume_flow(message, state, requirements)
    return handled


async def get_or_create_user(message: Message, telegram_user=None) -> str:
    db = get_supabase_client()
    identity = telegram_user or message.from_user
    tg_id = identity.id
    res = db.table("users").select("*").eq("telegram_user_id", tg_id).execute()
    if res.data:
        return res.data[0]['id']
    
    new_user = db.table("users").insert({
        "telegram_user_id": tg_id,
        "telegram_username": identity.username,
        "display_name": identity.full_name,
        "role": "RENTER"
    }).execute()
    return new_user.data[0]['id']

async def check_and_handle_active_searches(message: Message, state: FSMContext) -> bool:
    """Returns True if the limit is hit and we are awaiting confirmation, False otherwise."""
    user_id = await get_or_create_user(message)
    db = get_supabase_client()
    
    # Get active searches
    res = db.table("search_sessions").select("id").eq("user_id", user_id).eq("status", "ACTIVE").execute()
    active_count = len(res.data) if res.data else 0
    
    from app.config import settings
    if active_count >= settings.max_active_searches:
        current_result = db.table('search_sessions').select('id,version').eq('id', res.data[0].get('id')).eq('user_id', user_id).execute()
        current = current_result.data[0]
        pending = PendingRenterAction(
            action='replace_search',
            return_state=await state.get_state(),
            search_id=str(current.get('id')),
            search_version=int(current.get('version') or 1),
        )
        await _set_pending_action(
            message,
            state,
            pending,
            'You already have an active search. Starting over will cancel it. Do you want to replace it?',
        )
        tracer.log_event(
            'SEARCH_LIMIT_HIT',
            override_telegram_user_id=message.from_user.id,
            payload={'active_count': active_count, 'limit': settings.max_active_searches},
        )
        return True
    
    return False

# ─── COMMANDS (registered FIRST so they aren't swallowed by FSM states) ───

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if await check_and_handle_active_searches(message, state):
        return
        
    await state.clear()
    await message.answer(
        "👋 Hey there! I'm FlatHunter, your personal flat-finding assistant.\n\n"
        "Tell me what you're looking for — area, budget, type of place — "
        "and I'll start hunting for you!"
    )
    await state.set_state(RenterState.waiting_for_requirement)
    await state.update_data(chat_history=[])

@router.message(Command("cancel_search"))
async def cmd_cancel_search(message: Message, state: FSMContext):
    await _request_cancel_confirmation(message, state)

@router.message(Command("pause"))
async def cmd_pause_search(message: Message):
    user_id = await get_or_create_user(message)
    result = get_supabase_client().table("search_sessions").update({
        "status": "PAUSED",
        "paused_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id).eq("status", "ACTIVE").execute()
    await message.answer(
        "Your search is paused. I will not contact or notify you about new listings."
        if result.data else "You do not have an active search to pause."
    )


@router.message(Command("resume"))
async def cmd_resume_search(message: Message):
    user_id = await get_or_create_user(message)
    db = get_supabase_client()
    result = db.table("search_sessions").update({
        "status": "ACTIVE",
        "last_activated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id).eq("status", "PAUSED").execute()
    if not result.data:
        await message.answer("You do not have a paused search to resume.")
        return
    search = result.data[0]
    try:
        req_service._queue_match_job("MATCH_ACTIVE_SEARCH", search["id"], int(search.get("version") or 1), "RESUMED")
    except Exception:
        # The status update is already durable; a duplicate job is safe and transient
        # enqueue failure should be visible in server logs rather than misreported.
        import logging
        logging.getLogger(__name__).exception("Could not enqueue resumed search matching", extra={"search_id": search["id"]})
        await message.answer("Your search is active again. Matching will retry shortly.")
        return
    await message.answer("Your search is active again. I am checking listings added while it was paused.")
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
            "/cancel_search - Cancel your active search\n"
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
            "/cancel_search - Cancel your active search\n"
            "/help - Show this message"
        )
    else:
        help_text = (
            "👋 <b>Welcome to FlatHunter!</b>\n\n"
            "I help you find the perfect flat. Here's what I can do:\n\n"
            "<b>Commands:</b>\n"
            "/mysearch - Check status & matches of your active search\n"
            "/start - Start or restart your flat search\n"
            "/editsearch - Update your saved search\n"
            "/pause - Pause alerts and outreach\n"
            "/resume - Resume your paused search\n"
            "/cancel_search - Cancel your active search\n"
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
    session_res = db.table('search_sessions').select('id,status,created_at').eq('user_id', user_id).in_('status', ['ACTIVE', 'PAUSED', 'DRAFT']).order('created_at', desc=True).limit(1).execute()
    search_session = session_res.data[0] if session_res.data else None
    if not search_session:
        await message.answer("You don't have any active searches right now. Say <b>hi</b> or tap /start to start a search!", parse_mode="HTML")
        return

    search_id = search_session['id']
    
    # 3. Get requirements
    req_res = db.table("search_requirements").select("*").eq("search_id", search_id).execute()
    req = req_res.data[0] if req_res.data else {}
    
    # 4. Get matches summary
    matches_res = db.table("matches").select("id, status, fit_score").eq("search_id", search_id).execute()
    total_matches = len(matches_res.data)
    strong_matches = len([m for m in matches_res.data if m.get("status") == "STRONG_MATCH"])
    qualifying_matches = len([m for m in matches_res.data if m.get("status") == "NEEDS_QUALIFICATION"])
    
    status = escape(str(search_session.get('status') or 'UNKNOWN'))
    summary = (
        format_requirements(req, title='Your rental search')
        + f'\n\n<b>Status:</b> {status}'
        + f'\n<b>Matches:</b> {total_matches}'
        + f'\n<b>Strong matches:</b> {strong_matches}'
        + f'\n<b>Being clarified:</b> {qualifying_matches}'
        + '\n\nYou can tell me naturally what to change, or use /editsearch.'
    )
    await message.answer(summary, parse_mode="HTML")

@router.message(Command("editsearch"))
async def cmd_edit_search(message: Message, state: FSMContext):
    user_id = await get_or_create_user(message)
    try:
        _, current = req_service.get_editable_search(user_id)
    except ValueError as error:
        await message.answer(str(error))
        return
    await message.answer(
        format_requirements(current, title='Your current requirements')
        + '\n\nTell me what you want to change. For example: increase my budget to 25k and include Madhapur.'
    )
    await state.set_state(RenterState.waiting_for_search_edit)


@router.message(RenterState.waiting_for_search_edit)
async def process_search_edit(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer('Please send the change as text, for example: budget up to 25k.')
        return
    decision = await _classify_renter_turn(message, state)
    if RenterIntent.EDIT_REQUIREMENTS in decision.intents:
        await _handle_conversational_decision(message, state, decision)
        return
    if await _handle_conversational_decision(message, state, decision):
        return
    await message.answer('What part of your saved search would you like to change?')


@router.message(Command("set_availability"))
async def cmd_set_availability(message: Message, state: FSMContext):
    await message.answer("When are you generally free to visit properties? (e.g., 'Weekends anytime, weekdays after 6 PM')")
    await state.set_state(RenterState.waiting_for_availability)

# ─── GREETING CATCH (after commands, before FSM states) ───

@router.message(F.text.lower().in_({"hi", "hello", "hey", "get started", "hey bot"}))
async def greeting_start(message: Message, state: FSMContext):
    if await state.get_state():
        decision = await _classify_renter_turn(message, state)
        await _handle_conversational_decision(message, state, decision)
        return
    if await check_and_handle_active_searches(message, state):
        return
        
    await state.clear()
    await message.answer(
        "👋 Hey there! I'm FlatHunter, your personal flat-finding assistant.\n\n"
        "Tell me what you're looking for — area, budget, type of place — "
        "and I'll start hunting for you!"
    )
    await state.set_state(RenterState.waiting_for_requirement)
    await state.update_data(chat_history=[])

# ─── FSM STATE HANDLERS ───

@router.callback_query(F.data == CONFIRM_ACTION_CALLBACK)
async def confirm_conversational_action_callback(callback: CallbackQuery, state: FSMContext):
    await _confirm_pending_action(callback.message, state, callback.from_user)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == DECLINE_ACTION_CALLBACK)
async def decline_conversational_action_callback(callback: CallbackQuery, state: FSMContext):
    await _decline_pending_action(callback.message, state, callback.from_user)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()


@router.message(RenterState.confirming_conversational_action)
async def process_conversational_confirmation(message: Message, state: FSMContext):
    if not message.text:
        await message.answer('Please use the buttons, or reply yes or no.')
        return
    decision = await _classify_renter_turn(message, state)
    if not {RenterIntent.CONFIRM, RenterIntent.DECLINE}.intersection(decision.intents):
        await message.answer('Please confirm the pending action or tell me to keep the current search.', reply_markup=_confirmation_keyboard())
        return
    await _handle_conversational_decision(message, state, decision)


@router.message(RenterState.waiting_for_requirement)
async def process_requirement(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer('Please send your flat requirements as text. You can also ask what I have collected so far.')
        return
    decision = await _classify_renter_turn(message, state)
    if RenterIntent.REQUIREMENT_INPUT not in decision.intents:
        if await _handle_conversational_decision(message, state, decision):
            return
    show_after = RenterIntent.SHOW_REQUIREMENTS in decision.intents
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

        await state.update_data(parsed_reqs=parsed_reqs.model_dump(mode='json'))
        if show_after:
            await message.answer(format_requirements(parsed_reqs))
        
        if not parsed_reqs.is_complete or req_service.missing_core_requirements(parsed_reqs):
            bot_reply = parsed_reqs.follow_up_question or "Could you tell me a bit more about the area and budget you have in mind?"
            await message.answer(bot_reply)
            
            chat_history.append(f"Bot: {bot_reply}")
            await state.update_data(chat_history=chat_history)
            return
            
        summary = parsed_reqs.conversational_summary or "Got it! I have all the details I need."
        draft = req_service.create_draft_search(user_id, parsed_reqs, full_conversation)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Start Search", callback_data=f"search:start:{draft.id}")]
        ])
        
        await message.answer(
            f"{summary}\n\n"
            "Anything else you'd like me to keep in mind? "
            "(e.g. furnished, no brokerage, near metro, parking)\n\n"
            "Or click the button below to begin right away!",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        await state.update_data(
            chat_history=chat_history,
            parsed_reqs=parsed_reqs.model_dump(),
            user_id=user_id,
            full_conversation=full_conversation,
            search_id=str(draft.id)
        )
        await state.set_state(RenterState.collecting_extras)
        
    except Exception:
        logger.exception('Requirement collection failed')
        await message.answer('I had trouble processing that safely. Please rephrase it, or ask me to show what I have collected so far.')

@router.message(RenterState.collecting_extras)
async def process_extras(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer('Please send an extra preference as text, or say start searching.')
        return
    if message.text.strip().casefold() in DONE_PHRASES:
        await _activate_draft_from_state(message, state)
        return
    decision = await _classify_renter_turn(message, state)
    if RenterIntent.REQUIREMENT_INPUT not in decision.intents:
        if await _handle_conversational_decision(message, state, decision):
            return
    show_after = RenterIntent.SHOW_REQUIREMENTS in decision.intents
    data = await state.get_data()
    chat_history = data.get("chat_history", [])
    user_id = data.get("user_id")
    
    # Show typing immediately
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    chat_history.append(f"User: {message.text}")
    full_conversation = "\n".join(chat_history)
    
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            parsed_reqs = await req_service.parse_requirements(full_conversation)
        await state.update_data(parsed_reqs=parsed_reqs.model_dump(mode='json'))
        if show_after:
            await message.answer(format_requirements(parsed_reqs, title='Your updated requirements'))
        if req_service.missing_core_requirements(parsed_reqs):
            await message.answer(parsed_reqs.follow_up_question or "Please add the missing core search details before we start.")
            return
        search_id = data.get("search_id")
        if not search_id:
            raise ValueError("Your saved draft expired. Please restart with /start.")
        req_service.update_draft_search(user_id, search_id, parsed_reqs, full_conversation)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Start Search", callback_data=f"search:start:{search_id}")]
        ])
        
        summary = parsed_reqs.conversational_summary or "✅ Noted! I'll factor that into your search."
        
        await message.answer(
            f"{summary}\n\n"
            "Anything else? Or click below to begin! 🚀",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        chat_history.append(f"Bot: {summary}")
        await state.update_data(
            chat_history=chat_history,
            parsed_reqs=parsed_reqs.model_dump(),
            full_conversation=full_conversation,
            search_id=search_id
        )
        
    except Exception:
        logger.exception('Extra requirement collection failed')
        await message.answer('I could not save that preference safely. Please rephrase it, and I will keep your existing draft unchanged.')

@router.message(RenterState.waiting_for_availability)
async def process_availability(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer('Please describe your availability as text.')
        return
    decision = await _classify_renter_turn(message, state)
    if RenterIntent.SET_AVAILABILITY not in decision.intents:
        if await _handle_conversational_decision(message, state, decision):
            return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    user_id = await get_or_create_user(message)
    
    from app.scheduling.service import SchedulingService
    sched_service = SchedulingService()
    
    try:
        await sched_service.parse_and_save_availability(user_id, None, message.text)
        await message.answer("✅ Availability saved! I'll use this when landlords propose times.")
    except Exception:
        logger.exception('Availability collection failed')
        await message.answer('I could not save that availability. Try something like weekends anytime or weekdays after 6 PM.')
    finally:
        await state.clear()

@router.callback_query(F.data.startswith("search:start:"))
async def process_start_search_callback(callback: CallbackQuery, state: FSMContext):
    """Activate only the draft owned by the Telegram user pressing this button."""
    search_id = callback.data.removeprefix("search:start:")
    db = get_supabase_client()
    user_result = db.table("users").select("id").eq("telegram_user_id", callback.from_user.id).execute()
    if not user_result.data:
        await callback.answer("This confirmation is no longer valid. Please start again.", show_alert=True)
        return
    try:
        session = req_service.activate_search(user_result.data[0]["id"], search_id)
    except ValueError as error:
        await callback.message.answer(f"I could not start this search: {error}")
        await callback.answer()
        return
    tracer.log_event(
        event_type="SEARCH_STARTED",
        override_telegram_user_id=callback.from_user.id,
        payload={"search_id": str(session.id)},
        override_search_id=str(session.id),
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "Your search is live. I am checking the current Hyderabad inventory and will keep watching approved listings.",
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()
# ─── CALLBACK QUERIES ───

async def _callback_owns_search(callback: CallbackQuery, search_id: str) -> bool:
    """Authorize sensitive callbacks against persisted Telegram-user ownership."""
    db = get_supabase_client()
    user = db.table("users").select("id").eq("telegram_user_id", callback.from_user.id).execute()
    if not user.data:
        return False
    search = db.table("search_sessions").select("id").eq("id", search_id).eq("user_id", user.data[0]["id"]).execute()
    return bool(search.data)


def _match_id_from_callback(callback_data: str | None, action: str) -> str | None:
    """Extract and validate the UUID carried by a renter match action."""
    from uuid import UUID

    prefix = f"{action}_match_"
    if not callback_data or not callback_data.startswith(prefix):
        return None
    match_id = callback_data.removeprefix(prefix)
    try:
        UUID(match_id)
    except (TypeError, ValueError):
        return None
    return match_id


async def _callback_owned_match(callback: CallbackQuery, match_id: str) -> dict | None:
    db = get_supabase_client()
    result = db.table("matches").select(
        "id,search_id,listing_id,missing_information"
    ).eq("id", match_id).execute()
    if not result.data:
        return None
    match = result.data[0]
    if not await _callback_owns_search(callback, match["search_id"]):
        return None
    return match


@router.callback_query(F.data.startswith("details_match_"))
async def process_property_details_callback(callback: CallbackQuery):
    match_id = _match_id_from_callback(callback.data, "details")
    match = await _callback_owned_match(callback, match_id) if match_id else None
    if not match:
        await callback.answer("These property details are not available for your search.", show_alert=True)
        return

    listing_result = get_supabase_client().table("listings").select("*").eq(
        "id", match["listing_id"]
    ).execute()
    if not listing_result.data:
        await callback.answer("This property is no longer available.", show_alert=True)
        return

    await callback.answer()
    from app.matching.details import clarification_labels, draft_property_narrative, message_chunks

    narrative = await draft_property_narrative(listing_result.data[0])
    clarifications = clarification_labels(match.get("missing_information"))
    if clarifications:
        missing_section = (
            "Still to confirm with the owner or agent:\n"
            + "\n".join(f"• {item}" for item in clarifications)
            + "\n\nIf you choose Contact owner / agent, FlatHunter's agent will collect these clarifications "
              "and update you here."
        )
    else:
        missing_section = "No renter-specific owner clarifications are currently outstanding."

    response = f"Full property details\n\n{narrative}\n\n{missing_section}"
    for chunk in message_chunks(response):
        await callback.message.answer(chunk, parse_mode=None)


@router.callback_query(F.data.startswith("contact_match_"))
async def process_contact_callback(callback: CallbackQuery):
    from app.qualification.service import QualificationService

    match_id = _match_id_from_callback(callback.data, "contact")
    match = await _callback_owned_match(callback, match_id) if match_id else None
    if not match:
        await callback.answer("This action is not available for your search.", show_alert=True)
        return
    search_id, listing_id = match["search_id"], match["listing_id"]
    db = get_supabase_client()
    contacts_res = db.table("contacts").select("id").eq("listing_id", listing_id).execute()
    if not contacts_res.data:
        await callback.message.answer("This property does not have a contact listed.")
        await callback.answer()
        return

    qual_service = QualificationService()
    conv_id = qual_service.start_conversation(search_id, listing_id, contacts_res.data[0]["id"])
    await callback.message.answer("I have started qualifying this property and will update you here.")
    await qual_service.generate_initial_outreach(conv_id)
    await callback.answer()


@router.callback_query(F.data.startswith("visit_confirm_"))
async def process_visit_confirm(callback: CallbackQuery):
    visit_id = callback.data.removeprefix("visit_confirm_")
    db = get_supabase_client()
    visit = db.table("visits").select("search_id").eq("id", visit_id).execute()
    if not visit.data or not await _callback_owns_search(callback, visit.data[0]["search_id"]):
        await callback.answer("This visit is not available for your search.", show_alert=True)
        return
    from app.scheduling.service import SchedulingService
    SchedulingService().confirm_visit(visit_id)
    db.table("agent_jobs").insert({
        "job_type": "EMAIL_CONFIRM_VISIT", "status": "PENDING", "payload": {"visit_id": visit_id},
        "run_after": datetime.now(timezone.utc).isoformat(),
    }).execute()
    await callback.message.edit_text("✅ Visit confirmed! I have let the landlord know.")
    await callback.answer()


@router.callback_query(F.data.startswith("visit_decline_"))
async def process_visit_decline(callback: CallbackQuery):
    visit_id = callback.data.removeprefix("visit_decline_")
    db = get_supabase_client()
    visit = db.table("visits").select("search_id").eq("id", visit_id).execute()
    if not visit.data or not await _callback_owns_search(callback, visit.data[0]["search_id"]):
        await callback.answer("This visit is not available for your search.", show_alert=True)
        return
    from app.scheduling.service import SchedulingService
    SchedulingService().cancel_visit(visit_id)
    await callback.message.edit_text("❌ Visit declined. We will continue the search.")
    await callback.answer()


@router.callback_query(F.data.startswith("skip_match_"))
async def process_skip_callback(callback: CallbackQuery):
    match_id = _match_id_from_callback(callback.data, "skip")
    match = await _callback_owned_match(callback, match_id) if match_id else None
    if not match:
        await callback.answer("This result is not available for your search.", show_alert=True)
        return
    db = get_supabase_client()
    db.table("matches").update({"status": "SKIPPED"}).eq("id", match["id"]).execute()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Skipped. I will keep looking for a better fit.")
    await callback.answer()

# ─── FALLBACK (must be LAST) ───

@router.message()
async def renter_fallback(message: Message, state: FSMContext):
    if is_admin_menu_active(message.chat.id):
        return
    if not message.text or not message.text.strip():
        await message.answer('Please send text so I can help with your flat search, requirements, matches, or rental questions.')
        return
    decision = await _classify_renter_turn(message, state)
    if await _handle_conversational_decision(message, state, decision):
        await _remember_turn(state, 'user', message.text)
        tracer.log_event(
            'RENTER_TURN_ROUTED',
            override_telegram_user_id=message.from_user.id,
            payload={'intents': [item.value for item in decision.intents], 'confidence': decision.confidence},
        )
        return
    await message.answer('Tell me whether you want to start a search, view or edit your requirements, check matches, or ask a rental question.')
