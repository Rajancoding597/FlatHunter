from aiogram.fsm.state import State, StatesGroup

class RenterState(StatesGroup):
    waiting_for_requirement = State()
    confirming_requirement = State()
    waiting_for_availability = State()

class AdminState(StatesGroup):
    waiting_for_listing_info = State()
    confirming_listing = State()
