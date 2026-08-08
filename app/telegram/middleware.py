import time
import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, Message
from app.common.tracer import tracer

logger = logging.getLogger(__name__)


class TracingMiddleware(BaseMiddleware):
    """Aiogram middleware to trace incoming updates and set execution context."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Extract message and user information
        msg: Message = None
        if isinstance(event, Update):
            if event.message:
                msg = event.message
            elif event.callback_query and event.callback_query.message:
                msg = event.callback_query.message
        elif isinstance(event, Message):
            msg = event

        user_id = None
        user_name = "Unknown"
        
        if msg and msg.from_user:
            user_id = msg.from_user.id
            user_name = msg.from_user.full_name or msg.from_user.username or str(user_id)

        # Get FSM current state if available
        state = data.get("state")
        current_state_name = None
        if state:
            try:
                current_state_name = await state.get_state()
            except Exception:
                pass

        # Set user context for all downstream operations (including LLM calls)
        tracer.set_user_context(
            telegram_user_id=user_id,
            user_name=user_name,
            state=current_state_name
        )

        # Log incoming Telegram message
        if msg:
            text = msg.text or msg.caption or (f"[Photo ({len(msg.photo)} sizes)]" if msg.photo else "[Non-text message]")
            is_photo = bool(msg.photo)
            tracer.log_telegram_in(
                message_text=text,
                telegram_user_id=user_id or 0,
                user_name=user_name,
                message_id=msg.message_id,
                is_photo=is_photo
            )

        start_time = time.perf_counter()
        try:
            result = await handler(event, data)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000
            tracer.log_event(
                event_type="UNHANDLED_EXCEPTION",
                direction="INTERNAL",
                latency_ms=elapsed,
                status="ERROR",
                error=str(e),
                override_telegram_user_id=user_id,
                override_user_name=user_name
            )
            raise e
        finally:
            tracer.clear_user_context()
