from aiogram.fsm.state import State, StatesGroup


class RenterState(StatesGroup):
    waiting_for_requirement = State()
    collecting_extras = State()
    reviewing_requirements = State()
    confirming_requirement = State()
    waiting_for_availability = State()
    waiting_for_search_edit = State()
    confirming_conversational_action = State()


class AdminState(StatesGroup):
    waiting_for_listing_info = State()
    waiting_for_listing_media = State()
    confirming_listing = State()
