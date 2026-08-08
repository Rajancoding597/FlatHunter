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
    
    dp.include_router(admin_router)
    dp.include_router(renter_router)
    
    return bot, dp
