import pytest
from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault

from app.telegram import admin_handlers
from app.telegram.command_menus import (
    ADMIN_COMMANDS,
    ADMIN_RENTER_COMMANDS,
    RENTER_COMMANDS,
    activate_admin_menu,
    activate_renter_menu,
    initialize_command_menus,
    is_admin_menu_active,
    reset_menu_modes,
)


def command_names(commands):
    return [command.command for command in commands]


class FakeBot:
    def __init__(self):
        self.command_calls = []

    async def set_my_commands(self, commands, scope):
        self.command_calls.append((commands, scope))


@pytest.fixture(autouse=True)
def clear_menu_modes():
    reset_menu_modes()
    yield
    reset_menu_modes()


@pytest.mark.asyncio
async def test_startup_sets_complete_renter_menu_and_admin_switch_for_admin_chats():
    bot = FakeBot()

    await initialize_command_menus(bot, [101, 202])

    assert len(bot.command_calls) == 3
    default_commands, default_scope = bot.command_calls[0]
    assert isinstance(default_scope, BotCommandScopeDefault)
    assert command_names(default_commands) == command_names(RENTER_COMMANDS)
    assert command_names(default_commands) == [
        "start", "mysearch", "editsearch", "pause", "resume",
        "cancel_search", "set_availability", "help",
    ]
    for commands, scope in bot.command_calls[1:]:
        assert isinstance(scope, BotCommandScopeChat)
        assert command_names(commands) == command_names(ADMIN_RENTER_COMMANDS)
        assert command_names(commands)[-1] == "admin"
        assert is_admin_menu_active(scope.chat_id) is False


@pytest.mark.asyncio
async def test_admin_and_renter_menu_switches_are_chat_scoped_and_track_mode():
    bot = FakeBot()

    await activate_admin_menu(bot, 101)

    commands, scope = bot.command_calls[-1]
    assert isinstance(scope, BotCommandScopeChat)
    assert scope.chat_id == 101
    assert command_names(commands) == command_names(ADMIN_COMMANDS)
    assert "sim_reply" in command_names(commands)
    assert "renter" in command_names(commands)
    assert is_admin_menu_active(101) is True

    await activate_renter_menu(bot, 101, admin_can_switch=True)

    commands, scope = bot.command_calls[-1]
    assert command_names(commands) == command_names(ADMIN_RENTER_COMMANDS)
    assert is_admin_menu_active(101) is False


class FakeState:
    def __init__(self):
        self.cleared = False
        self.states = []

    async def clear(self):
        self.cleared = True

    async def set_state(self, state):
        self.states.append(state)


class FakeMessage:
    def __init__(self, bot, chat_id=101, user_id=101, chat_type="private"):
        self.bot = bot
        self.chat = type("Chat", (), {"id": chat_id, "type": chat_type})()
        self.from_user = type("User", (), {"id": user_id})()
        self.answers = []

    async def answer(self, text, **_kwargs):
        self.answers.append(text)


@pytest.mark.asyncio
async def test_mode_commands_switch_menu_and_renter_switch_does_not_start_search(monkeypatch):
    async def fake_get_or_create_user(_message):
        return "user-1"

    bot = FakeBot()
    message = FakeMessage(bot)
    admin_state = FakeState()
    monkeypatch.setattr(admin_handlers, "get_or_create_user", fake_get_or_create_user)

    await admin_handlers.cmd_admin_mode(message, admin_state)

    assert is_admin_menu_active(101) is True
    assert command_names(bot.command_calls[-1][0]) == command_names(ADMIN_COMMANDS)
    assert admin_state.cleared is True

    renter_state = FakeState()
    await admin_handlers.cmd_renter_mode(message, renter_state)

    assert is_admin_menu_active(101) is False
    assert command_names(bot.command_calls[-1][0]) == command_names(ADMIN_RENTER_COMMANDS)
    assert renter_state.cleared is True
    assert renter_state.states == []
    assert "/start" in message.answers[-1]


@pytest.mark.asyncio
async def test_admin_router_mode_predicate_changes_with_menu(monkeypatch):
    bot = FakeBot()
    message = FakeMessage(bot)
    monkeypatch.setattr(admin_handlers.settings, "admin_telegram_ids", [101])

    assert admin_handlers.is_admin_interface(message) is False

    await activate_admin_menu(bot, 101)
    assert admin_handlers.is_admin_interface(message) is True

    await activate_renter_menu(bot, 101, admin_can_switch=True)
    assert admin_handlers.is_admin_interface(message) is False


@pytest.mark.asyncio
async def test_admin_menu_cannot_be_exposed_to_an_entire_group_chat():
    bot = FakeBot()
    message = FakeMessage(bot, chat_id=-100123, chat_type="group")
    state = FakeState()

    await admin_handlers.cmd_admin_mode(message, state)

    assert bot.command_calls == []
    assert state.cleared is False
    assert "private chat" in message.answers[-1]
