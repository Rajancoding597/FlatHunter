import os
import json
import logging
from datetime import datetime, timezone
from contextvars import ContextVar
from typing import Any, Optional, Dict

logger = logging.getLogger(__name__)

# Context variable storing active user context for the current async task
user_context_var: ContextVar[Dict[str, Any]] = ContextVar("user_context", default={})

LOGS_DIR = os.path.join(os.getcwd(), "logs")
USERS_LOGS_DIR = os.path.join(LOGS_DIR, "users")
SEARCHES_LOGS_DIR = os.path.join(LOGS_DIR, "searches")
TRACES_FILE = os.path.join(LOGS_DIR, "traces.jsonl")


def _ensure_log_dirs():
    os.makedirs(USERS_LOGS_DIR, exist_ok=True)
    os.makedirs(SEARCHES_LOGS_DIR, exist_ok=True)


class Tracer:
    """Thread-safe and async-safe event tracer writing to JSONL files."""

    @staticmethod
    def set_user_context(telegram_user_id: Optional[int] = None, user_name: Optional[str] = None, state: Optional[str] = None, search_id: Optional[str] = None):
        """Set the active user context for the current async execution."""
        user_context_var.set({
            "telegram_user_id": telegram_user_id,
            "user_name": user_name,
            "state": state,
            "search_id": str(search_id) if search_id else None
        })

    @staticmethod
    def get_user_context() -> Dict[str, Any]:
        """Get the active user context."""
        return user_context_var.get({})

    @staticmethod
    def clear_user_context():
        """Reset the active user context."""
        user_context_var.set({})

    @staticmethod
    def log_event(
        event_type: str,
        direction: str = "INTERNAL",
        payload: Optional[Dict[str, Any]] = None,
        latency_ms: Optional[float] = None,
        status: str = "SUCCESS",
        error: Optional[str] = None,
        override_telegram_user_id: Optional[int] = None,
        override_user_name: Optional[str] = None,
        override_search_id: Optional[str] = None
    ):
        """Record an event to traces.jsonl, the user's specific jsonl file, and the search specific jsonl file."""
        _ensure_log_dirs()
        
        ctx = Tracer.get_user_context()
        tg_id = override_telegram_user_id or ctx.get("telegram_user_id")
        user_name = override_user_name or ctx.get("user_name")
        state = ctx.get("state")
        search_id = override_search_id or ctx.get("search_id")

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "telegram_user_id": tg_id,
            "user_name": user_name,
            "state": state,
            "search_id": search_id,
            "event_type": event_type,
            "direction": direction,
            "status": status,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "payload": payload or {},
            "error": error
        }

        json_line = json.dumps(record, default=str) + "\n"

        try:
            # 1. Append to global traces.jsonl
            with open(TRACES_FILE, "a", encoding="utf-8") as f:
                f.write(json_line)

            # 2. Append to user-specific file if telegram_user_id is available
            if tg_id:
                user_file = os.path.join(USERS_LOGS_DIR, f"{tg_id}.jsonl")
                with open(user_file, "a", encoding="utf-8") as f:
                    f.write(json_line)
                    
            # 3. Append to search-specific file if search_id is available
            if search_id:
                search_file = os.path.join(SEARCHES_LOGS_DIR, f"{search_id}.jsonl")
                with open(search_file, "a", encoding="utf-8") as f:
                    f.write(json_line)
        except Exception as e:
            logger.error(f"Failed to write trace record: {e}")

    @staticmethod
    def log_telegram_in(message_text: str, telegram_user_id: int, user_name: str, message_id: int, is_photo: bool = False):
        Tracer.log_event(
            event_type="TELEGRAM_MESSAGE",
            direction="INBOUND",
            payload={
                "message_id": message_id,
                "text": message_text,
                "is_photo": is_photo
            },
            override_telegram_user_id=telegram_user_id,
            override_user_name=user_name
        )

    @staticmethod
    def log_telegram_out(reply_text: str, telegram_user_id: int, user_name: Optional[str] = None):
        Tracer.log_event(
            event_type="TELEGRAM_REPLY",
            direction="OUTBOUND",
            payload={
                "text": reply_text
            },
            override_telegram_user_id=telegram_user_id,
            override_user_name=user_name
        )

    @staticmethod
    def log_llm_call(
        provider: str,
        model: str,
        prompt: Any,
        response: Any,
        latency_ms: float,
        status: str = "SUCCESS",
        error: Optional[str] = None,
        schema_name: Optional[str] = None
    ):
        # Clean prompt presentation for logging
        if isinstance(prompt, list):
            prompt_data = [str(p)[:500] if not isinstance(p, str) else p for p in prompt]
        else:
            prompt_data = str(prompt)

        # Convert Pydantic object or dict to json-serializable structure
        if hasattr(response, "model_dump"):
            resp_data = response.model_dump()
        elif hasattr(response, "dict"):
            resp_data = response.dict()
        elif isinstance(response, dict):
            resp_data = response
        else:
            resp_data = str(response) if response is not None else None

        Tracer.log_event(
            event_type="LLM_CALL",
            direction="OUTBOUND",
            latency_ms=latency_ms,
            status=status,
            error=error,
            payload={
                "provider": provider,
                "model": model,
                "schema": schema_name,
                "prompt": prompt_data,
                "response": resp_data
            }
        )

tracer = Tracer()
