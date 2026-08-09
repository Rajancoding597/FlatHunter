from aiogram.fsm.state import State, StatesGroup


class RenterState(StatesGroup):
    waiting_for_requirement = State()
    collecting_extras = State()
    confirming_requirement = State()
    confirming_new_search_override = State()
    waiting_for_availability = State()
    waiting_for_search_edit = State()


class AdminState(StatesGroup):
    waiting_for_listing_info = State()
    waiting_for_listing_media = State()
    confirming_listing = State()
