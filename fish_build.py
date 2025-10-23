#!/usr/bin/env python3
# ... keep the existing imports at top of file ...

import time
import traceback

# reuse a simple retry helper
def call_with_retries(func, retries=3, delay=2, backoff=2, *args, **kwargs):
    attempt = 0
    cur_delay = delay
    while attempt < retries:
        try:
            return func(*args, **kwargs), None
        except Exception as e:
            attempt += 1
            print(f"[Retry] attempt {attempt} failed: {e}")
            if attempt >= retries:
                return None, e
            time.sleep(cur_delay)
            cur_delay *= backoff

def write_status(out_dir, success: bool, message: str):
    import json, os
    status = {
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "success": bool(success),
        "message": message,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

def build_payload():
    # Ensure API key present
    if not MS_API_KEY or MS_API_KEY.startswith("PASTE-"):
        raise RuntimeError("METEOSOURCE_API_KEY is missing or placeholder; cannot fetch Meteosource data.")

    ms = Meteosource(MS_API_KEY, MS_TIER)

    print("Requesting Meteosource hourly forecast...")
    fc, err = call_with_retries(lambda: ms.get_point_forecast(lat=LAT, lon=LON, tz=TZ, lang=langs.ENGLISH, units=units.METRIC, sections=(sections.HOURLY,)), retries=3, delay=2)
    if err:
        raise RuntimeError(f"Meteosource request failed after retries: {err}")

    hourly = fc.hourly.data or []
    if not hourly:
        return {"generated_at_local": datetime.now().strftime("%a %d %b %H:%M"), "windows": []}

    # WorldTides may be optional — but call with retries if key present
    wt = {"heights": [], "extremes": []}
    if WT_KEY:
        try:
            print("Requesting WorldTides...")
            wt_res, wt_err = call_with_retries(lambda: fetch_worldtides(LAT, LON, datetime.utcnow(), WT_DAYS, WT_STEP_S, WT_KEY), retries=2, delay=3)
            if wt_err:
                print(f"WorldTides failed (non-fatal): {wt_err}")
            else:
                wt = wt_res or wt
        except Exception as e:
            print(f"WorldTides final error: {e}")

    # ... rest of original build_payload logic unchanged ...
    return payload

def main():
    ap = argparse.ArgumentParser(description="Build a fishing forecast card (Whitegate) with WorldTides.")
    ap.add_argument("--out", default=OUT_DIR, help="Output dir (e.g., dist/fishing)")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    try:
        payload = build_payload()
        with open(os.path.join(out, "fishing.tmp.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(os.path.join(out, "fishing.tmp.json"), os.path.join(out, "fishing.json"))
        with open(os.path.join(out, "card.tmp.html"), "w", encoding="utf-8") as f:
            f.write(render_card(payload))
        os.replace(os.path.join(out, "card.tmp.html"), os.path.join(out, "card.html"))
        write_status(out, True, "Success")
    except Exception as e:
        tb = traceback.format_exc()
        msg = f"Build failed: {e}\n{tb}"
        print(msg)
        write_status(out, False, msg)
        raise
