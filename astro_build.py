#!/usr/bin/env python3
# ... keep the existing imports at top of file ...

import time
import traceback

# --- helper: simple retry wrapper for API calls ---
def call_with_retries(func, retries=3, delay=2, backoff=2, *args, **kwargs):
    """Call func with retries. Returns (result, None) or (None, exception)."""
    attempt = 0
    cur_delay = delay
    while attempt < retries:
        try:
            return func(*args, **kwargs), None
        except Exception as e:
            attempt += 1
            err_text = f"Attempt {attempt}/{retries} failed: {e}"
            print(err_text)
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

def main():
    ap = argparse.ArgumentParser(description="Build astro + weather (separate) JSON/HTML cards with tooltips.")
    ap.add_argument("--out", default="./dist/astro", help="Output folder for ASTRO card (e.g., dist/astro)")
    args = ap.parse_args()
    astro_out = os.path.abspath(args.out)
    os.makedirs(astro_out, exist_ok=True)
    out_base = os.path.dirname(astro_out)
    weather_out = os.path.join(out_base, "weather")
    os.makedirs(weather_out, exist_ok=True)

    # Validate API key
    if not API_KEY or API_KEY.startswith("PASTE-"):
        msg = "METEOSOURCE_API_KEY is missing or placeholder. Please set METEOSOURCE_API_KEY in the environment."
        print(msg)
        write_status(astro_out, False, msg)
        raise SystemExit(1)

    ms = Meteosource(API_KEY, TIER)

    # Try the forecast call with retries
    print("Requesting hourly forecast from Meteosource...")
    fc, err = call_with_retries(lambda: ms.get_point_forecast(lat=LAT, lon=LON, tz=TZ, lang=langs.ENGLISH, units=units.METRIC, sections=(sections.HOURLY,)), retries=3, delay=2)
    if err:
        tb = traceback.format_exc()
        msg = f"Meteosource get_point_forecast failed after retries: {err}\n{tb}"
        print(msg)
        write_status(astro_out, False, msg)
        raise SystemExit(1)

    # proceed as before...
    try:
        geo = Geo(LAT, LON, ELEV_M)
        target_ra_dec = (TARGET_RA, TARGET_DEC) if (TARGET_RA and TARGET_DEC) else None
        windows = build_night_windows_from_hourly(fc.hourly, geo)
        # ... rest of logic unchanged ...
        # After writing files:
        write_status(astro_out, True, "Success")
    except Exception as e:
        tb = traceback.format_exc()
        msg = f"Build failed during processing: {e}\n{tb}"
        print(msg)
        write_status(astro_out, False, msg)
        raise
