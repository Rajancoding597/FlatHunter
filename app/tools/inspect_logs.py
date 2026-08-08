import os
import json
import time
import argparse
from datetime import datetime

LOGS_DIR = os.path.join(os.getcwd(), "logs")
TRACES_FILE = os.path.join(LOGS_DIR, "traces.jsonl")
USERS_LOGS_DIR = os.path.join(LOGS_DIR, "users")


def format_event(record: dict) -> str:
    """Format a JSON trace event into a clean, colored terminal view."""
    ts = record.get("timestamp", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            ts_str = dt.strftime("%H:%M:%S")
        except Exception:
            ts_str = ts[:19]
    else:
        ts_str = "--:--:--"

    tg_id = record.get("telegram_user_id") or "SYSTEM"
    user_name = record.get("user_name") or ""
    event_type = record.get("event_type", "")
    direction = record.get("direction", "")
    status = record.get("status", "SUCCESS")
    latency = f"{record.get('latency_ms', 0):.0f}ms" if record.get("latency_ms") is not None else ""
    error = record.get("error")
    payload = record.get("payload", {})

    # Safe ASCII headers
    status_tag = f"[{status}]" if status == "SUCCESS" else f"[ERROR - {status}]"
    
    header = f"=== [{ts_str}] User: {user_name} ({tg_id}) | {event_type} ({direction}) {latency} {status_tag} ==="
    lines = [header]

    if event_type == "TELEGRAM_MESSAGE":
        lines.append(f"  [USER] Message: {payload.get('text')}")
    elif event_type == "TELEGRAM_REPLY":
        lines.append(f"  [BOT]  Reply: {payload.get('text')}")
    elif event_type == "LLM_CALL":
        provider = payload.get("provider", "").upper()
        model = payload.get("model", "")
        schema = payload.get("schema") or "Raw Text"
        lines.append(f"  [LLM]  Provider: {provider} ({model}) | Schema: {schema}")
        
        prompt = payload.get("prompt", "")
        if prompt:
            prompt_snip = prompt[:150].replace('\n', ' ') + ("..." if len(prompt) > 150 else "")
            lines.append(f"  [LLM]  Prompt: {prompt_snip}")
            
        resp = payload.get("response")
        if resp:
            if isinstance(resp, dict):
                resp_json = json.dumps(resp, indent=2)
                lines.append(f"  [LLM]  Response JSON:")
                for l in resp_json.split("\n"):
                    lines.append(f"         {l}")
            else:
                lines.append(f"  [LLM]  Response: {str(resp)[:200]}")
    
    if error:
        lines.append(f"  [WARN] Error: {error}")

    lines.append("-" * 65 + "\n")
    return "\n".join(lines)


def inspect_logs(user_id: str = None, event_type: str = None, errors_only: bool = False, limit: int = 50):
    """Read and filter log traces."""
    if not os.path.exists(TRACES_FILE):
        print(f"No traces found at {TRACES_FILE}. Run the bot and send some messages first!")
        return

    # If user_id is provided, check if a dedicated user file exists
    target_file = TRACES_FILE
    if user_id:
        user_specific_file = os.path.join(USERS_LOGS_DIR, f"{user_id}.jsonl")
        if os.path.exists(user_specific_file):
            target_file = user_specific_file

    matched = []
    with open(target_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                
                # Filters
                if user_id and str(record.get("telegram_user_id")) != str(user_id):
                    continue
                if event_type and record.get("event_type") != event_type:
                    continue
                if errors_only and record.get("status") == "SUCCESS":
                    continue
                    
                matched.append(record)
            except Exception:
                continue

    if not matched:
        print("No matching trace logs found.")
        return

    # Display the most recent `limit` entries
    matched = matched[-limit:]
    print(f"\nFound {len(matched)} matching event(s):\n")
    for rec in matched:
        print(format_event(rec))


def tail_logs():
    """Live tail traces.jsonl in real-time."""
    if not os.path.exists(TRACES_FILE):
        os.makedirs(os.path.dirname(TRACES_FILE), exist_ok=True)
        open(TRACES_FILE, "a").close()

    print(f"👀 Live watching traces at {TRACES_FILE}... (Press Ctrl+C to stop)\n")
    with open(TRACES_FILE, "r", encoding="utf-8") as f:
        # Go to the end of the file
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                print(format_event(rec))
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="FlatHunter Log & Trace Inspector")
    parser.add_argument("--user", type=str, help="Filter by Telegram User ID")
    parser.add_argument("--type", type=str, choices=["TELEGRAM_MESSAGE", "TELEGRAM_REPLY", "LLM_CALL", "UNHANDLED_EXCEPTION"], help="Filter by event type")
    parser.add_argument("--errors", action="store_true", help="Show only errors")
    parser.add_argument("--limit", type=int, default=20, help="Number of records to show (default: 20)")
    parser.add_argument("--tail", action="store_true", help="Live tail trace logs in real-time")

    args = parser.parse_args()

    if args.tail:
        tail_logs()
    else:
        inspect_logs(
            user_id=args.user,
            event_type=args.type,
            errors_only=args.errors,
            limit=args.limit
        )


if __name__ == "__main__":
    main()
