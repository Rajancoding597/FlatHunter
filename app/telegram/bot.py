import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from app.config import settings
from .renter_handlers import router as renter_router
from .admin_handlers import router as admin_router

logger = logging.getLogger(__name__)

async def init_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    
    # Register tracing middleware for all incoming updates
    from .middleware import TracingMiddleware
    dp.update.outer_middleware(TracingMiddleware())
    
    dp.include_router(admin_router)
    dp.include_router(renter_router)
    
    # Set bot commands
    from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
    
    default_commands = [
        BotCommand(command="mysearch", description="Check status & matches of your search"),
        BotCommand(command="start", description="Start or restart your flat search"),
        BotCommand(command="cancel_search", description="Cancel your active search"),
        BotCommand(command="pause", description="Pause alerts and outreach"),
        BotCommand(command="resume", description="Resume your search"),
        BotCommand(command="editsearch", description="Update your saved search"),
        BotCommand(command="set_availability", description="Set your availability for visits"),
        BotCommand(command="help", description="Show available commands"),
    ]
    await bot.set_my_commands(default_commands, scope=BotCommandScopeDefault())
    
    admin_commands = [
        BotCommand(command="addlisting", description="Add a new property"),
        BotCommand(command="bulkadd", description="Add multiple properties"),
        BotCommand(command="renter", description="Test renter flow in this chat"),
        BotCommand(command="admin", description="Return to admin actions"),
        BotCommand(command="status", description="View system metrics"),
        BotCommand(command="viewsearches", description="View active renters' searches"),
        BotCommand(command="viewlistings", description="View recent active listings"),
        BotCommand(command="viewdrafts", description="View pending drafts"),
        BotCommand(command="help", description="Show admin commands"),
    ]
    
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.warning(f"Failed to set admin commands for {admin_id}: {e}")
    
    return bot, dp
