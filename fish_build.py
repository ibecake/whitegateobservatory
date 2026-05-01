#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, json, argparse, time
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict
from datetime import datetime, timedelta, timezone
from statistics import mean
from math import sin, radians, copysign
from zoneinfo import ZoneInfo

import requests
import ephem
from pymeteosource.api import Meteosource
from pymeteosource.types import tiers, sections, langs, units

# ── Config ────────────────────────────────────────────────────────────────────
MS_API_KEY  = os.environ.get("METEOSOURCE_API_KEY", "PASTE-METEOSOURCE-KEY")
MS_TIER     = tiers.FLEXI
LAT, LON    = 51.8268, -8.2321    # Whitegate
TZ          = "Europe/Dublin"
_DUBLIN_TZ  = ZoneInfo(TZ)

WT_KEY      = os.environ.get("WORLD_TIDES_KEY")  # required for tides
WT_DAYS     = 7
WT_STEP_S   = 3600  # 1h height resolution keeps credit usage low

# Optional marine overrides until you wire a marine API (waves/sea temp)
OVERRIDE_WAVE_H = os.environ.get("WAVE_H")   # metres
OVERRIDE_WAVE_T = os.environ.get("WAVE_T")   # seconds
OVERRIDE_SEA_T  = os.environ.get("SEA_TEMP") # °C

OUT_DIR   = "dist/fishing"

# ── Card styling moved to dashboard.css ───────────────────────────────────────
def shared_card_css() -> str:
    return ""  # All CSS now in dashboard.css

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
    try:    return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except: return dt

class Geo:
    def __init__(self, lat, lon, elev_m=5):
        self.obs = ephem.Observer(); self.obs.lat=str(lat); self.obs.lon=str(lon); self.obs.elevation=elev_m
    def sun(self, dt_utc): self.obs.date = dt_utc; return ephem.Sun(self.obs)

# ── Species by month (editable) ───────────────────────────────────────────────
SPECIES_BY_MONTH = {
    1: ["codling", "whiting", "flounder", "dab", "rockling", "coalfish"],
    2: ["codling", "whiting", "flounder", "dab", "rockling", "coalfish"],
    3: ["flounder", "dab", "plaice (starting)", "codling (late)", "bass (odd/mild spells)"],
    4: ["bass (starting)", "plaice", "flounder", "dab", "dogfish"],
    5: ["bass", "pollack", "wrasse", "thornback ray (starting)", "smoothhound (late May+)", "mackerel (first/patchy)"],
    6: ["mackerel", "bass", "pollack", "wrasse", "thornback ray", "smoothhound"],
    7: ["mackerel (peak)", "bass", "pollack", "wrasse", "garfish", "mullet", "thornback ray"],
    8: ["bass", "mullet", "mackerel", "pollack", "wrasse", "thornback ray"],
    9: ["bass (often strong)", "pollack (often bigger)", "mackerel (tail)", "thornback ray (tail)"],
    10: ["codling (starting)", "whiting (starting)", "coalfish", "bass (early month)", "thornback ray (tail)"],
    11: ["codling", "whiting", "coalfish", "flounder", "dab", "rockling"],
    12: ["codling", "whiting", "coalfish", "flounder", "dab", "rockling", "conger (pier/rocks, night)"],
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

# NEW: tide helpers
def get_tide_times_for_day(day_date, extremes: List[Dict]) -> Tuple[Optional[str], Optional[str]]:
    """Find all high and low tide times for a given day (in local time)."""
    high_times = []
    low_times = []

    for ex in extremes:
        ex_date = ex['dt'].astimezone(_DUBLIN_TZ).date()
        if ex_date == day_date:
            time_str = ex['dt'].astimezone(_DUBLIN_TZ).strftime("%H:%M")
            if ex['type'] == 'High':
                high_times.append(time_str)
            elif ex['type'] == 'Low':
                low_times.append(time_str)

    return (
        " / ".join(high_times) if high_times else None,
        " / ".join(low_times) if low_times else None,
    )

def score_tide(dt_local: datetime, heights: List[Dict], extremes: List[Dict]) -> Tuple[float,str]:
    """heights: [{'dt':datetime,'height':float}] step=1h
       extremes: [{'dt':datetime,'type':'High'|'Low','height':float}]"""
    if not heights:
        return 60.0, "tide:?"

    # find nearest heights around dt to estimate rate (m/h)
    idx = None
    for i, h in enumerate(heights):
        if h["dt"] >= dt_local:
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
        if ex["dt"] >= dt_local:
            next_ext = ex
            break
    prev_ext = None
    for ex in reversed(extremes):
        if ex["dt"] <= dt_local:
            prev_ext = ex
            break

    def hours_to(ex):
        return abs((ex["dt"] - dt_local).total_seconds())/3600.0 if ex else None

    h_next = hours_to(next_ext)
    next_lab = f"next {next_ext['type'].lower()} in {h_next:.1f}h" if next_ext and h_next is not None else "next:?"

    # moving water is good; give strong bonus to flood (rising tide is best)
    s_move = min(100.0, 40.0 + min(60.0, abs(rate)*300.0))  # 0 m/h->40, 0.2 m/h->100
    s_phase = 15.0 if rate>0.03 else -10.0  # strong flood bias, penalty for ebb
    s_slack_pen = -20.0 if phase=="slack" else 0.0

    # Prefer fishing 1-3 hours after low tide (flood tide)
    timing_bonus = 0.0
    if prev_ext and prev_ext["type"] == "Low":
        h_since_low = (dt_local - prev_ext["dt"]).total_seconds() / 3600.0
        if 1.0 <= h_since_low <= 3.0:
            timing_bonus += 20.0  # prime flood tide window
        elif 0.5 <= h_since_low < 1.0 or 3.0 < h_since_low <= 4.0:
            timing_bonus += 10.0  # still good
    elif h_next is not None:
        if next_ext["type"] == "High" and h_next <= 1.5:
            timing_bonus += 8.0  # approaching high tide

    score = max(0.0, min(100.0, s_move + s_phase + s_slack_pen + timing_bonus))
    note = f"tide={phase} {rate:+.2f} m/h, {next_lab}"
    return score, note

# Weights (sum 1.0) — includes tides
WEIGHTS = dict(
    wind=0.22, clouds=0.07, precip=0.07, pressure=0.07, humidity=0.04,
    wave=0.18, seatemp=0.05, tide=0.30
)

# Shared datetime format used by the tide chart builder
_TIDE_DT_FMT = "%a %d %b %H:%M"

# ── Tide chart data builder ───────────────────────────────────────────────────
def _build_tide_chart_data(heights: List[Dict], extremes: List[Dict]) -> dict:
    """Build Chart.js-compatible data from hourly tide heights and extremes.

    Returns a dict with:
      labels     – ISO-format datetime strings (one per hourly sample)
      heights    – matching tide-height values (metres)
      high_data  – sparse array with height values only at High-tide extremes
      low_data   – sparse array with height values only at Low-tide extremes
    """
    labels = [h["dt"].astimezone(_DUBLIN_TZ).strftime(_TIDE_DT_FMT) for h in heights]
    height_values = [round(h["height"], 2) for h in heights]

    high_data: List[Optional[float]] = [None] * len(labels)
    low_data:  List[Optional[float]] = [None] * len(labels)

    label_to_idx = {lbl: i for i, lbl in enumerate(labels)}
    for ex in extremes:
        lbl = ex["dt"].astimezone(_DUBLIN_TZ).strftime(_TIDE_DT_FMT)
        idx = label_to_idx.get(lbl)
        if idx is None and heights:
            # fall back to the nearest hourly sample
            idx = min(
                range(len(heights)),
                key=lambda i: abs((heights[i]["dt"] - ex["dt"]).total_seconds()),
            )
        if idx is not None:
            if ex["type"] == "High":
                high_data[idx] = round(ex["height"], 2)
            elif ex["type"] == "Low":
                low_data[idx] = round(ex["height"], 2)

    return {
        "labels":    labels,
        "heights":   height_values,
        "high_data": high_data,
        "low_data":  low_data,
    }

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
    if not key:
        return dict(heights=[], extremes=[])
    start_ts = int(time.mktime(start_utc.timetuple()))
    length_s = int(days * 86400)

    # 1) Extremes (high/low times)
    p_ext = dict(extremes="", lat=lat, lon=lon, start=start_ts, length=length_s, key=key)
    j_ext = wt_request(p_ext) or {}
    extremes = []
    for ex in j_ext.get("extremes", []) or []:
        # ex: {"dt": 1609459200, "date":"2021-01-01T00:00+00:00","height":3.12,"type":"High"}
        ts = ex.get("dt") or ex.get("time")  # some payloads use "dt" or "time"
        if ts is None: continue
        extremes.append(dict(
            dt=datetime.fromtimestamp(int(ts), tz=timezone.utc),
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
            dt=datetime.fromtimestamp(int(ts), tz=timezone.utc),
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
    start_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    wt = fetch_worldtides(LAT, LON, start_utc, WT_DAYS, WT_STEP_S, WT_KEY)

    heights = wt.get("heights", [])
    extremes = wt.get("extremes", [])

    geo = Geo(LAT, LON)
    by_time = {h.date: h for h in hourly if getattr(h, "date", None)}

    rows = []
    for h in hourly:
        dt_local = getattr(h, "date", None)
        if not dt_local: continue
        dt_utc = to_utc(dt_local)

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

        s_tide, n_tide = score_tide(dt_local, heights, extremes) if WT_KEY else (60.0, "tide:? (no key)")

        # evening/dusk bonus (best fishing as it gets dark)
        sun = geo.sun(dt_utc); alt = float(sun.alt)*180.0/3.141592653589793
        hour = dt_local.hour
        evening_bonus = 0.0
        if -6.0 < alt < +6.0:  # twilight period
            if 15 <= hour <= 21:  # evening hours
                evening_bonus = 12.0
            else:  # morning twilight
                evening_bonus = 4.0
        elif -12.0 < alt <= -6.0:  # nautical twilight, darker
            if 15 <= hour <= 22:
                evening_bonus = 8.0

        score = (
            WEIGHTS["wind"]*s_w + WEIGHTS["clouds"]*s_c + WEIGHTS["precip"]*s_r +
            WEIGHTS["pressure"]*s_p + WEIGHTS["humidity"]*s_hu + WEIGHTS["wave"]*s_wav +
            WEIGHTS["seatemp"]*s_sst + WEIGHTS["tide"]*s_tide + evening_bonus
        )
        score = max(0.0, min(100.0, score))

        rows.append(dict(
            t=dt_local, score=score,
            notes="; ".join([n_w,n_c,n_r,n_p,n_wav,n_sst,n_tide]),
            wind_speed=ws,
            wind_gust=gust
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
            # Extract wind data from first hour of window
            w0 = win[0]
            best.append((s, t0, t1, w0))
        best.sort(key=lambda x: x[0], reverse=True)
        top = best[:1]
        # targets by month
        month = hrs[0]["t"].month
        targets = ", ".join(SPECIES_BY_MONTH.get(month, [])) or "—"
        
        # Get tide times for this day
        day_date = hrs[0]["t"].date()
        high_tide, low_tide = get_tide_times_for_day(day_date, extremes) if WT_KEY else (None, None)
        
        for s, t0, t1, w0 in top:
            cls = "GOOD" if s>=70 else "FAIR" if s>=55 else "POOR"
            # Extract wind and gust from notes
            wind_speed = "—"
            gust_speed = "—"
            if "wind_gust" in w0:
                wind_speed = f"{w0['wind_speed']:.1f}" if w0['wind_speed'] is not None else "—"
                gust_speed = f"{w0['wind_gust']:.1f}" if w0['wind_gust'] is not None else "—"
            
            windows.append(dict(
                day_label = t0.strftime("%a %d %b"),
                start = t0.strftime("%H:%M"),
                end   = t1.strftime("%H:%M"),
                score = int(round(s)),
                cls   = cls,
                targets = targets,
                high_tide = high_tide or "—",
                low_tide = low_tide or "—",
                wind = wind_speed,
                gust = gust_speed
            ))

    tide_chart = (
        _build_tide_chart_data(heights, extremes) if heights
        else {"labels": [], "heights": [], "high_data": [], "low_data": []}
    )

    return {
        "generated_at_local": datetime.now().strftime("%a %d %b %H:%M"),
        "windows": windows,
        "tide_chart": tide_chart,
    }

# ── Tide graph HTML builder ───────────────────────────────────────────────────
def _render_tide_graph(tide_chart: dict) -> str:
    """Return an HTML card containing a Chart.js tide-height graph.

    The graph shows:
      • A filled area-line for hourly tide heights.
      • Orange scatter points for High-tide extremes.
      • Sky-blue scatter points for Low-tide extremes.
    Chart.js is loaded from a CDN so no additional Python package is required.
    """
    chart_json = json.dumps(tide_chart)
    return f"""<div class="card" style="margin-top:16px">
  <div class="h">Cork Harbour — Tide Heights</div>
  <div class="sub">Hourly tide heights (metres).
    <span aria-hidden="true">●</span><span class="sr-only">Orange dot:</span> High tide
    &nbsp;<span aria-hidden="true">●</span><span class="sr-only">Blue dot:</span> Low tide
  </div>
  <div style="position:relative;width:100%;height:260px">
    <canvas id="tideChart"
      aria-label="Tide height chart for Cork Harbour showing hourly heights in metres with high and low tide markers"
      role="img"></canvas>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
(function(){{
  var raw = {chart_json};
  if (!raw.labels || !raw.labels.length) return;

  // Show a tick label every 24 hours (one per day)
  var tickLabels = raw.labels.map(function(lbl, i) {{
    return (i % 24 === 0) ? lbl.slice(0, 9) : "";
  }});

  new Chart(document.getElementById("tideChart"), {{
    type: "line",
    data: {{
      labels: raw.labels,
      datasets: [
        {{
          label: "Tide height (m)",
          data: raw.heights,
          borderColor: "#3b82f6",
          backgroundColor: "rgba(59,130,246,0.12)",
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          tension: 0.4,
          order: 3
        }},
        {{
          label: "High tide",
          data: raw.high_data,
          type: "scatter",
          backgroundColor: "#f97316",
          borderColor: "#f97316",
          pointRadius: 5,
          pointHoverRadius: 7,
          showLine: false,
          spanGaps: false,
          order: 1
        }},
        {{
          label: "Low tide",
          data: raw.low_data,
          type: "scatter",
          backgroundColor: "#0ea5e9",
          borderColor: "#0ea5e9",
          pointRadius: 5,
          pointHoverRadius: 7,
          showLine: false,
          spanGaps: false,
          order: 2
        }}
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: "index", intersect: false }},
      plugins: {{
        legend: {{ position: "bottom", labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
        tooltip: {{
          callbacks: {{
            title: function(items) {{ return items[0].label; }},
            label: function(item) {{
              var v = item.raw;
              if (v === null || v === undefined) return null;
              return item.dataset.label + ": " + v.toFixed(2) + " m";
            }}
          }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{
            callback: function(val, idx) {{ return tickLabels[idx]; }},
            maxRotation: 0,
            autoSkip: false,
            font: {{ size: 11 }}
          }},
          grid: {{ color: "rgba(0,0,0,0.05)" }}
        }},
        y: {{
          title: {{ display: true, text: "Height (m)", font: {{ size: 11 }} }},
          ticks: {{ font: {{ size: 11 }} }},
          grid: {{ color: "rgba(0,0,0,0.05)" }}
        }}
      }}
    }}
  }});
}})();
</script>"""

# ── Render HTML card ──────────────────────────────────────────────────────────
def render_card(payload: dict) -> str:
    js  = shared_card_js("fishing-card-size")
    updated = payload["generated_at_local"]
    wins = payload["windows"]
    tide_chart = payload.get("tide_chart", {"labels": [], "heights": [], "high_data": [], "low_data": []})

    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="../assets/css/dashboard.css">
</head>
<body style="margin:0;padding:16px;background:transparent">
'''

    colgroup = (
        "<colgroup>"
        "<col style='width:11ch'>"  # Date
        "<col style='width:18ch'>"  # Window
        "<col style='width:6ch'>"   # Score
        "<col style='width:7ch'>"   # Class
        "<col style='width:26ch'>"  # Targets
        "<col style='width:8ch'>"   # High Tide
        "<col style='width:8ch'>"   # Low Tide
        "<col style='width:7ch'>"   # Wind
        "<col style='width:7ch'>"   # Gust
        "</colgroup>"
    )

    if not wins:
        rows = "<tr><td colspan='9' class='dim'>No data</td></tr>"
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
                f"<td class='num'>{w['high_tide']}</td>"
                f"<td class='num'>{w['low_tide']}</td>"
                f"<td class='num'>{w['wind']}</td>"
                f"<td class='num'>{w['gust']}</td>"
                "</tr>"
            )

    html += (
        '<div id="fish-root" class="wrap"><div class="card">'
        '<div class="h">Whitegate Fishing Forecast — Best Times & Targets</div>'
        f'<div class="sub">Updated {updated}. Score blends wind, clouds, rain, pressure trend, humidity, waves, sea temp'
        + (", tides via WorldTides" if WT_KEY else ", tides (no key)") + '.</div>'
        '<div class="tblwrap"><table>'
        f"{colgroup}"
        '<thead><tr><th>Date</th><th>Best 2-hour Window</th><th class="num">Score</th><th>Class</th><th>Suggested Targets</th><th class="num">High Tide</th><th class="num">Low Tide</th><th class="num">Wind (m/s)</th><th class="num">Gust (m/s)</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
        '<div class="credit">Check Irish regs before fishing.</div>'
        '</div>'
        + _render_tide_graph(tide_chart)
        + '</div>' + js + '</body></html>'
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
