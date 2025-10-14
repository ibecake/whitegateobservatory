#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, json, argparse, time
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict
from datetime import datetime, timedelta, timezone
from statistics import mean
from math import sin, radians

import requests
import ephem
from pymeteosource.api import Meteosource
from pymeteosource.types import tiers, sections, langs, units

# ── Config ────────────────────────────────────────────────────────────────────
MS_API_KEY  = os.environ.get("METEOSOURCE_API_KEY", "PASTE-METEOSOURCE-KEY")
MS_TIER     = tiers.FLEXI
LAT, LON    = 51.8268, -8.2321    # Whitegate
TZ          = "Europe/Dublin"

WT_KEY      = os.environ.get("WORLD_TIDES_KEY")  # required for tides
WT_DAYS     = 7
WT_STEP_S   = 3600  # 1h height resolution keeps credit usage low

# Optional marine overrides until you wire a marine API (waves/sea temp)
OVERRIDE_WAVE_H = os.environ.get("WAVE_H")   # metres
OVERRIDE_WAVE_T = os.environ.get("WAVE_T")   # seconds
OVERRIDE_SEA_T  = os.environ.get("SEA_TEMP") # °C

OUT_DIR   = "dist/fishing"

# ── Card styling (matches your astro/weather cards) ───────────────────────────
def shared_card_css() -> str:
    return """
<style>
  :root{
    --astro-font: system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif;
    --astro-bg: #ffffff; --astro-fg: #0f172a; --astro-sub: #64748b;
    --astro-border: #e5e7eb; --astro-shadow: 0 2px 10px rgba(0,0,0,.06);
    --astro-radius: 12px; --badge-good:#16a34a; --badge-fair:#ca8a04; --badge-poor:#dc2626;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --astro-bg:#0b1020; --astro-fg:#e5e7eb; --astro-sub:#9aa4b2; --astro-border:#1f2937;
      --astro-shadow: 0 8px 30px rgba(0,0,0,.45);
    }
  }
  .wrap{font-family:var(--astro-font); background:transparent;}
  .card{max-width:980px; border:1px solid var(--astro-border); border-radius:var(--astro-radius);
        padding:16px; background:var(--astro-bg); box-shadow:var(--astro-shadow); color:var(--astro-fg);}
  .h{font-weight:700; font-size:18px; margin:0 0 6px}
  .sub{color:var(--astro-sub); font-size:12px; margin-bottom:12px}
  .credit{margin-top:8px; color:var(--astro-sub); font-size:11px}
  .tblwrap{overflow:auto}
  table{width:100%; border-collapse:collapse; min-width:720px; background:var(--astro-bg); color:var(--astro-fg); table-layout:fixed}
  th, td{padding:10px; border-top:1px solid var(--astro-border); text-align:left; font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:middle}
  thead th{border-bottom:1px solid var(--astro-border); color:var(--astro-sub); font-size:12px; letter-spacing:.02em; text-transform:uppercase}
  td.num, th.num{text-align:right}
  .badge{border-radius:999px; padding:2px 8px; font-size:12px; color:#fff; display:inline-block; white-space:nowrap}
  .GOOD{background:var(--badge-good)} .FAIR{background:var(--badge-fair)} .POOR{background:var(--badge-poor)}
  .dim{color:var(--astro-sub)}
  @media (max-width: 900px){
    /* Hide Details to keep rows compact */
    .card table thead th:nth-child(6), .card table tbody td:nth-child(6){ display:none; }
  }
  @media (max-width: 640px){
    /* Keep: Date, Window, Score, Class, Targets */
    .card th, .card td{ padding:6px; font-size:12px; line-height:1.15 }
    .badge{ padding:1px 6px; font-size:11px }
  }
</style>
"""

def shared_card_js(message_type: str) -> str:
    return f"""
<script>
(function(){{
  var p = new URLSearchParams(location.search);
  if (p.get("transparent")==="1") document.body.style.background="transparent";
  function send(){{ try{{ parent.postMessage({{type:"{message_type}", height: document.documentElement.scrollHeight}}, "*"); }}catch(e){{}} }}
  window.addEventListener("load", send); setTimeout(send,60); setTimeout(send,300);
}})();
</script>
"""

# ── Helpers ───────────────────────────────────────────────────────────────────
def to_utc(dt):
    try:    return dt.astimezone(timezone.utc).replace(tzinfo=None)  # naive UTC
    except: return dt

class Geo:
    def __init__(self, lat, lon, elev_m=5):
        self.obs = ephem.Observer(); self.obs.lat=str(lat); self.obs.lon=str(lon); self.obs.elevation=elev_m
    def sun(self, dt_utc): self.obs.date = dt_utc; return ephem.Sun(self.obs)

# ── Species by month (editable) ───────────────────────────────────────────────
SPECIES_BY_MONTH = {
    1:["flounder","whiting","codling (boat)"],
    2:["flounder","whiting","codling (boat)"],
    3:["flounder","pollack (some)","early bass (odd)"],
    4:["bass","pollack","wrasse"],
    5:["bass","pollack","wrasse","mackerel (first)"],
    6:["bass","pollack","wrasse","mackerel","garfish","mullet"],
    7:["bass","pollack","wrasse","mackerel","garfish","mullet"],
    8:["bass","pollack","wrasse","mackerel","garfish","mullet"],
    9:["bass","pollack","wrasse","mackerel (tail)","mullet"],
    10:["bass","pollack","wrasse (tail)","mackerel (odd)"],
    11:["bass","pollack (boat)","flounder","codling (start)"],
    12:["flounder","bass (odd)","codling (boat)"],
}

# ── Component scoring ─────────────────────────────────────────────────────────
def score_wind(ws, gust):
    if ws is None: return 60, "wind:?"
    base = 100 if ws<=2 else 90 if ws<=4 else 75 if ws<=6 else 55 if ws<=8 else 35 if ws<=12 else 15
    if gust is not None and gust>10: base -= 10
    return max(0,base), f"wind={ws:.1f}m/s{(f', gust={gust:.1f}' if gust is not None else '')}"

def score_cloud(total):
    if total is None: return 60, "cloud:?"
    t = float(total); peak=50.0
    s = max(0, 100 - abs(t-peak)*1.2)
    return s, f"cloud={int(round(t))}%"

def score_precip(mm):
    if mm is None: return 80, "rain:?"
    if mm==0: return 100, "rain=0"
    if mm<=0.2: return 70, f"rain={mm:.2f}mm"
    return 30, f"rain={mm:.2f}mm"

def score_pressure_trend(p_now, p_prev3h):
    if p_now is None or p_prev3h is None: return 60, "ΔP:?"
    dp = p_now - p_prev3h
    s = 85 if -2.0 <= dp <= 2.0 else 70 if -4.0 <= dp <= 4.0 else 45
    return s, f"ΔP={dp:+.1f} hPa/3h"

def score_humidity(h):
    if h is None: return 70, "rh:?"
    s = 85 if 40<=h<=85 else 70 if 30<=h<=90 else 55
    return s, f"rh={int(round(h))}%"

def score_wave(height_m: Optional[float], period_s: Optional[float], ws: Optional[float]):
    if height_m is None and period_s is None:
        if ws is None: return 70, "wave:?"
        return (65 if ws<=6 else 45 if ws<=10 else 25), f"wave≈(wind {ws:.1f}m/s)"
    h = height_m if height_m is not None else 0.8
    t = period_s if period_s is not None else 7.0
    s_h = 100 if h<=0.5 else 85 if h<=0.8 else 70 if h<=1.2 else 45 if h<=1.8 else 20
    s_t = 60 if t<5 else 85 if t<=10 else 75
    s = 0.6*s_h + 0.4*s_t
    return s, f"wave={h:.1f}m/{t:.0f}s"

def score_sea_temp(tC: Optional[float], month:int):
    if tC is None: 
        return 70, "SST:?"
    s = 85 if 10<=tC<=17 else 95 if tC>17 else 60 if 8<=tC<10 else 45
    return s, f"SST={tC:.1f}°C"

# NEW: tide score (expects dt in **naive UTC** to match WorldTides parsing)
def score_tide(dt_utc: datetime, heights: List[Dict], extremes: List[Dict]) -> Tuple[float,str]:
    """dt_utc: naive UTC datetime
       heights: [{'dt':datetime(UTC-naive),'height':float}] step=1h
       extremes: [{'dt':datetime(UTC-naive),'type':'High'|'Low','height':float}]"""
    if not heights:
        return 60.0, "tide:?"

    # find nearest heights around dt to estimate rate (m/h)
    idx = None
    for i, h in enumerate(heights):
        if h["dt"] >= dt_utc:
            idx = i
            break
    if idx is None: idx = len(heights)-1
    i0 = max(0, idx-1); i1 = min(len(heights)-1, idx+1)
    h0, h1 = heights[i0], heights[i1]
    dt_h = (h1["dt"] - h0["dt"]).total_seconds()/3600.0 or 1.0
    rate = (h1["height"] - h0["height"]) / dt_h  # m/h
    phase = "flood" if rate>0.03 else "ebb" if rate<-0.03 else "slack"

    # distance to next extreme
    next_ext = None
    for ex in extremes:
        if ex["dt"] >= dt_utc:
            next_ext = ex
            break

    def hours_to(ex):
        return abs((ex["dt"] - dt_utc).total_seconds())/3600.0 if ex else None

    h_next = hours_to(next_ext)
    next_lab = f"next {next_ext['type'].lower()} in {h_next:.1f}h" if next_ext and h_next is not None else "next:?"

    # moving water is good; bonus for flood; slack penalty
    s_move = min(100.0, 40.0 + min(60.0, abs(rate)*300.0))  # 0 m/h->40, 0.2 m/h->100
    s_phase = 5.0 if rate>0.03 else 0.0
    s_slack_pen = -15.0 if phase=="slack" else 0.0

    timing_bonus = 0.0
    if h_next is not None:
        if next_ext["type"] == "High":
            if h_next <= 2.0: timing_bonus += 6.0
        else:  # Low
            if h_next <= 2.0: timing_bonus += 10.0

    score = max(0.0, min(100.0, s_move + s_phase + s_slack_pen + timing_bonus))
    note = f"tide={phase} {rate:+.2f} m/h, {next_lab}"
    return score, note

# Weights (sum 1.0) — includes tides
WEIGHTS = dict(
    wind=0.25, clouds=0.08, precip=0.08, pressure=0.08, humidity=0.05,
    wave=0.20, seatemp=0.06, tide=0.20
)

# ── WorldTides fetch ──────────────────────────────────────────────────────────
def wt_request(params: dict) -> Optional[dict]:
    url = "https://www.worldtides.info/api"
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            print(f"[WorldTides] HTTP {r.status_code}: {r.text[:120]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[WorldTides] error: {e}")
        return None

def fetch_worldtides(lat: float, lon: float, start_utc: datetime, days: int, step_s: int=3600, key: Optional[str]=None):
    """
    Returns UTC-naive datetimes for both heights and extremes (using utcfromtimestamp),
    so that we can compare against our own UTC-naive times (dt_utc).
    """
    if not key:
        return dict(heights=[], extremes=[])
    start_ts = int(time.mktime(start_utc.timetuple()))
    length_s = int(days * 86400)

    # 1) Extremes (high/low times)
    p_ext = dict(extremes="", lat=lat, lon=lon, start=start_ts, length=length_s, key=key)
    j_ext = wt_request(p_ext) or {}
    extremes = []
    for ex in j_ext.get("extremes", []) or []:
        ts = ex.get("dt") or ex.get("time")
        if ts is None: continue
        extremes.append(dict(
            dt=datetime.utcfromtimestamp(int(ts)),  # <-- UTC, naive
            type=ex.get("type",""),
            height=float(ex.get("height", 0.0))
        ))

    # 2) Heights (hourly)
    p_h = dict(heights="", lat=lat, lon=lon, start=start_ts, length=length_s, step=step_s, key=key)
    j_h = wt_request(p_h) or {}
    heights = []
    for h in j_h.get("heights", []) or []:
        ts = h.get("dt") or h.get("time")
        if ts is None: continue
        heights.append(dict(
            dt=datetime.utcfromtimestamp(int(ts)),  # <-- UTC, naive
            height=float(h.get("height", 0.0))
        ))

    heights.sort(key=lambda x: x["dt"])
    extremes.sort(key=lambda x: x["dt"])
    return dict(heights=heights, extremes=extremes)

# ── Build (fetch + score + windows) ───────────────────────────────────────────
def build_payload():
    ms = Meteosource(MS_API_KEY, MS_TIER)
    fc = ms.get_point_forecast(lat=LAT, lon=LON, tz=TZ, lang=langs.ENGLISH, units=units.METRIC,
                               sections=(sections.HOURLY,))
    hourly = fc.hourly.data or []
    if not hourly:
        return {"generated_at_local": datetime.now().strftime("%a %d %b %H:%M"), "windows":[]}

    # WorldTides: start from now-6h (to ensure prev points) for WT_DAYS
    start_utc = datetime.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    wt = fetch_worldtides(LAT, LON, start_utc, WT_DAYS, WT_STEP_S, WT_KEY)

    heights = wt.get("heights", [])
    extremes = wt.get("extremes", [])

    geo = Geo(LAT, LON)
    by_time = {h.date: h for h in hourly if getattr(h, "date", None)}

    rows = []
    for h in hourly:
        dt_local = getattr(h, "date", None)
        if not dt_local: continue
        dt_utc = to_utc(dt_local)  # <-- naive UTC for all tide comparisons

        # weather vars
        ws   = getattr(getattr(h,"wind",None), "speed", None)
        gust = getattr(getattr(h,"wind",None), "gusts", None)
        cloud= getattr(getattr(h,"cloud_cover",None), "total", None)
        rain = getattr(getattr(h,"precipitation",None), "total", None)
        pres = getattr(h, "pressure", None)
        rh   = getattr(h, "humidity", None)

        prev = by_time.get(dt_local - timedelta(hours=3))
        pres_prev = getattr(prev, "pressure", None) if prev else None

        # marine overrides
        wave_h = float(OVERRIDE_WAVE_H) if OVERRIDE_WAVE_H else None
        wave_t = float(OVERRIDE_WAVE_T) if OVERRIDE_WAVE_T else None
        sst    = float(OVERRIDE_SEA_T)  if OVERRIDE_SEA_T  else None

        # component scores
        s_w,   n_w   = score_wind(ws, gust)
        s_c,   n_c   = score_cloud(cloud)
        s_r,   n_r   = score_precip(rain)
        s_p,   n_p   = score_pressure_trend(pres, pres_prev)
        s_hu,  n_hu  = score_humidity(rh)
        s_wav, n_wav = score_wave(wave_h, wave_t, ws)
        s_sst, n_sst = score_sea_temp(sst, month=dt_local.month)

        s_tide, n_tide = score_tide(dt_utc, heights, extremes) if WT_KEY else (60.0, "tide:? (no key)")

        # dawn/dusk bonus
        sun = geo.sun(dt_utc); alt = float(sun.alt)*180.0/3.141592653589793
        dawn_dusk_bonus = 6.0 if (-12.0 < alt < +6.0) else 0.0

        score = (
            WEIGHTS["wind"]*s_w + WEIGHTS["clouds"]*s_c + WEIGHTS["precip"]*s_r +
            WEIGHTS["pressure"]*s_p + WEIGHTS["humidity"]*s_hu + WEIGHTS["wave"]*s_wav +
            WEIGHTS["seatemp"]*s_sst + WEIGHTS["tide"]*s_tide + dawn_dusk_bonus
        )
        score = max(0.0, min(100.0, score))

        rows.append(dict(
            t=dt_local, score=score,
            notes="; ".join([n_w,n_c,n_r,n_p,n_wav,n_sst,n_tide]),
        ))

    # 2h windows per day
    by_day: Dict[str, List[dict]] = {}
    for r in rows:
        key = r["t"].date().isoformat()
        by_day.setdefault(key, []).append(r)

    windows = []
    for day, hrs in sorted(by_day.items()):
        hrs = sorted(hrs, key=lambda x: x["t"])
        best = []
        for i in range(len(hrs)-1):
            win = hrs[i:i+2]
            s = mean([w["score"] for w in win])
            t0, t1 = win[0]["t"], win[-1]["t"] + timedelta(minutes=59)
            best.append((s, t0, t1, "; ".join(w["notes"] for w in win)))
        best.sort(key=lambda x: x[0], reverse=True)
        top = best[:3]
        # targets by month
        month = hrs[0]["t"].month
        targets = ", ".join(SPECIES_BY_MONTH.get(month, [])) or "—"
        for s, t0, t1, note in top:
            cls = "GOOD" if s>=75 else "FAIR" if s>=60 else "POOR"
            windows.append(dict(
                day_label = t0.strftime("%a %d %b"),
                start = t0.strftime("%H:%M"),
                end   = t1.strftime("%H:%M"),
                score = int(round(s)),
                cls   = cls,
                targets = targets,
                details = note
            ))

    return {
        "generated_at_local": datetime.now().strftime("%a %d %b %H:%M"),
        "windows": windows
    }

# ── Render HTML card ──────────────────────────────────────────────────────────
def render_card(payload: dict) -> str:
    css = shared_card_css()
    js  = shared_card_js("fishing-card-size")
    updated = payload["generated_at_local"]
    wins = payload["windows"]

    colgroup = (
        "<colgroup>"
        "<col style='width:11ch'>"  # Date
        "<col style='width:18ch'>"  # Window
        "<col style='width:6ch'>"   # Score
        "<col style='width:7ch'>"   # Class
        "<col style='width:26ch'>"  # Targets
        "<col>"                     # Details (flex)
        "</colgroup>"
    )

    if not wins:
        rows = "<tr><td colspan='6' class='dim'>No data</td></tr>"
    else:
        rows = ""
        for w in wins:
            rows += (
                "<tr>"
                f"<td>{w['day_label']}</td>"
                f"<td>{w['start']}–{w['end']}</td>"
                f"<td class='num'><strong>{w['score']}</strong></td>"
                f"<td><span class='badge {w['cls']}'>{w['cls']}</span></td>"
                f"<td class='dim'>{w['targets']}</td>"
                f"<td class='dim' title='{w['details']}'>{w['details']}</td>"
                "</tr>"
            )

    html = (
        css +
        '<div id="fish-root" class="wrap"><div class="card">'
        '<div class="h">Whitegate Fishing Forecast — Best Times & Targets</div>'
        f'<div class="sub">Updated {updated}. Score blends wind, clouds, rain, pressure trend, humidity, waves, sea temp'
        + (", tides via WorldTides" if WT_KEY else ", tides (no key)") + '.</div>'
        '<div class="tblwrap"><table>'
        f"{colgroup}"
        '<thead><tr><th>Date</th><th>Best 2-hour Window</th><th class="num">Score</th><th>Class</th><th>Suggested Targets</th><th>Details</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
        '<div class="credit">Weather data © Meteosource • Tides © WorldTides • Check Irish regs before fishing.</div>'
        '</div></div>' + js
    )
    return html

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Build a fishing forecast card (Whitegate) with WorldTides.")
    ap.add_argument("--out", default=OUT_DIR, help="Output dir (e.g., dist/fishing)")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    payload = build_payload()

    with open(os.path.join(out, "fishing.tmp.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(os.path.join(out, "fishing.tmp.json"), os.path.join(out, "fishing.json"))

    with open(os.path.join(out, "card.tmp.html"), "w", encoding="utf-8") as f:
        f.write(render_card(payload))
    os.replace(os.path.join(out, "card.tmp.html"), os.path.join(out, "card.html"))

    print(f"Wrote FISHING: {os.path.join(out,'fishing.json')}  /  {os.path.join(out,'card.html')}")

if __name__ == "__main__":
    main()
