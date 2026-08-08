from aiogram.fsm.state import State, StatesGroup

class RenterState(StatesGroup):
    waiting_for_requirement = State()
    collecting_extras = State()
    confirming_requirement = State()
    confirming_new_search_override = State()
    waiting_for_availability = State()

class AdminState(StatesGroup):
    waiting_for_listing_info = State()
    confirming_listing = State()
