from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from app.config import settings
from .renter_handlers import router as renter_router
from .admin_handlers import router as admin_router

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
    
    from .command_menus import initialize_command_menus

    await initialize_command_menus(bot, settings.admin_telegram_ids)
    
    return bot, dp
