#!/usr/bin/env python3
import os
import json
from datetime import datetime
import time
import traceback

def call_with_retries(func, retries=3, delay=2, backoff=2, *args, **kwargs):
    attempt = 0
    cur_delay = delay
    while attempt < retries:
        try:
            result = func(*args, **kwargs)
            return result, None
        except Exception as e:
            attempt += 1
            print(f"[Retry] attempt {attempt} failed: {e}")
            if attempt >= retries:
                return None, e
            time.sleep(cur_delay)
            cur_delay *= backoff

def write_status(out_dir, success: bool, message: str):
    status = {
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "success": bool(success),
        "message": message,
        "next_update": (datetime.utcnow().timestamp() + 14400)  # 4 hours
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

# Add cache busting headers
def write_json_with_headers(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Add timestamp to force refresh
    timestamp = int(datetime.utcnow().timestamp())
    cache_buster = f"{filepath}?v={timestamp}"
    return cache_buster
