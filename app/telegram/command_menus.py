"""Telegram command-menu modes for renter-first operation and explicit admin access."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

logger = logging.getLogger(__name__)

RENTER_COMMANDS = (
    BotCommand(command="start", description="Start or restart your flat search"),
    BotCommand(command="mysearch", description="View your search and matches"),
    BotCommand(command="editsearch", description="Update your saved search"),
    BotCommand(command="pause", description="Pause alerts and outreach"),
    BotCommand(command="resume", description="Resume your paused search"),
    BotCommand(command="cancel_search", description="Cancel your active search"),
    BotCommand(command="set_availability", description="Set your availability for visits"),
    BotCommand(command="help", description="Show renter commands"),
)

ADMIN_COMMANDS = (
    BotCommand(command="addlisting", description="Add a new property"),
    BotCommand(command="bulkadd", description="Add multiple properties"),
    BotCommand(command="status", description="View system metrics"),
    BotCommand(command="viewsearches", description="View active renter searches"),
    BotCommand(command="viewlistings", description="View listing inventory"),
    BotCommand(command="viewdrafts", description="Review and edit pending drafts"),
    BotCommand(command="version", description="View deployed bot version"),
    BotCommand(command="sim_reply", description="Simulate an owner reply"),
    BotCommand(command="renter", description="Switch to renter controls"),
    BotCommand(command="help", description="Show admin commands"),
)

ADMIN_RENTER_COMMANDS = RENTER_COMMANDS + (
    BotCommand(command="admin", description="Switch to admin controls"),
)

_admin_menu_chat_ids: set[int] = set()


def is_admin_menu_active(chat_id: int) -> bool:
    return chat_id in _admin_menu_chat_ids


async def activate_admin_menu(bot, chat_id: int) -> None:
    """Switch one authorized private chat to admin commands after Telegram accepts it."""
    await bot.set_my_commands(list(ADMIN_COMMANDS), scope=BotCommandScopeChat(chat_id=chat_id))
    _admin_menu_chat_ids.add(chat_id)


async def activate_renter_menu(bot, chat_id: int, *, admin_can_switch: bool) -> None:
    """Show renter commands, plus /admin only for configured administrators."""
    commands = ADMIN_RENTER_COMMANDS if admin_can_switch else RENTER_COMMANDS
    await bot.set_my_commands(list(commands), scope=BotCommandScopeChat(chat_id=chat_id))
    _admin_menu_chat_ids.discard(chat_id)


async def initialize_command_menus(bot, admin_ids: Iterable[int]) -> None:
    """Reset process/menu state so every chat starts renter-first after a restart."""
    _admin_menu_chat_ids.clear()
    await bot.set_my_commands(list(RENTER_COMMANDS), scope=BotCommandScopeDefault())
    for admin_id in admin_ids:
        try:
            await activate_renter_menu(bot, int(admin_id), admin_can_switch=True)
        except Exception:
            logger.exception("Could not initialize renter-first menu for admin", extra={"admin_id": admin_id})


def reset_menu_modes() -> None:
    """Clear local menu-mode state for startup and deterministic tests."""
    _admin_menu_chat_ids.clear()
