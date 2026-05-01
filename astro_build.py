#!/usr/bin/env python3
# Build static JSON + HTML card for Whitegate Observatory astro nights.
# Run on a schedule (e.g., cron every 4h). No DB needed.

from __future__ import annotations
import os, json, argparse
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from statistics import mean
from datetime import timedelta, datetime, timezone

import ephem
from pymeteosource.api import Meteosource
from pymeteosource.types import tiers, sections, langs, units

# ── Config (edit these) ───────────────────────────────────────────────────────
API_KEY  = os.environ.get("METEOSOURCE_API_KEY", "PASTE-YOUR-API-KEY-HERE")
TIER     = tiers.FLEXI
LAT, LON = 51.8268, -8.2321        # Whitegate, Co. Cork
ELEV_M   = 20
TZ       = "Europe/Dublin"

SUNSET_BUFFER_H  = 1.0
SUNRISE_BUFFER_H = 1.0

# Optional target (improves brightness model). Leave None if generic.
TARGET_RA  = None  # e.g. "05:35:17"
TARGET_DEC = None  # e.g. "-05:23:28"

BASELINE_SQM = 20.8

# Weights for hourly score (0..100)
W_CLOUDS, W_VIS, W_DEWSPREAD, W_WIND, W_PRECIP, W_BRIGHT = 0.40, 0.10, 0.15, 0.10, 0.05, 0.20

# ── Helpers ───────────────────────────────────────────────────────────────────
def _get(obj, path: str, default=None):
    cur = obj
    for part in path.split("."):
        if cur is None: return default
        try: cur = getattr(cur, part)
        except Exception: return default
    return default if cur is None else cur

def _to_utc(dt):
    try:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return dt

def _pct(x):  # percent
    try: return float(x)
    except: return None

def _km(x):
    try: return float(x)
    except: return None

def _ms(x):
    try: return float(x)
    except: return None

def _mm(x):
    try: return float(x)
    except: return None

def _c(x):
    try: return float(x)
    except: return None

def airmass(alt_deg: float) -> float:
    z = max(0.0, 90.0 - alt_deg)
    if z >= 90.0: return 38.0
    # Kasten & Young 1989
    return 1.0 / (ephem.cos(ephem.degrees(z * ephem.degree)) + 0.50572 * ((96.07995 - z) ** -1.6364))

class Geo:
    def __init__(self, lat, lon, elev_m):
        self.obs = ephem.Observer()
        self.obs.lat = str(lat); self.obs.lon = str(lon); self.obs.elevation = elev_m
    def compute(self, dt_utc, target_ra_dec: Optional[Tuple[str,str]]):
        self.obs.date = dt_utc
        moon = ephem.Moon(self.obs); sun = ephem.Sun(self.obs)
        star = None
        if target_ra_dec and target_ra_dec[0] and target_ra_dec[1]:
            star = ephem.FixedBody()
            star._ra = ephem.hours(target_ra_dec[0]); star._dec = ephem.degrees(target_ra_dec[1])
            star.compute(self.obs)
        return sun, moon, star

def clouds_score(h):
    low, mid, high, total = _pct(_get(h,"cloud_cover.low")), _pct(_get(h,"cloud_cover.middle")), _pct(_get(h,"cloud_cover.high")), _pct(_get(h,"cloud_cover.total"))
    if low is not None or mid is not None or high is not None:
        v = (0.6*(low if low is not None else (total or 0.0)) +
             0.3*(mid if mid is not None else (total or 0.0)) +
             0.1*(high if high is not None else (total or 0.0)))
        return max(0.0, 100.0 - v), f"clouds={v:.0f}%"
    if total is not None:
        return max(0.0, 100.0 - total), f"clouds={total:.0f}%"
    return 50.0, "clouds=unknown"

def visibility_score(h):
    vis = _km(_get(h,"visibility"))
    if vis is None: return 60.0, "vis=unknown"
    lo, hi = 5.0, 25.0
    s = (vis - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, s)), f"vis={vis:.1f}km"

def dewspread_score(h):
    t, dp = _c(_get(h,"temperature")), _c(_get(h,"dew_point")) or _c(_get(h,"dewpoint"))
    if t is None or dp is None: return 60.0, "ΔT=unknown", None
    spread = t - dp
    lo, hi = 0.0, 8.0
    s = max(0.0, min(100.0, (spread - lo)/(hi - lo) * 100.0))
    return s, f"ΔT={spread:.1f}°C", spread

def wind_score(h):
    ws, gust = _ms(_get(h,"wind.speed")), _ms(_get(h,"wind.gusts"))
    if ws is None: return 60.0, "wind=unknown"
    s = 100.0 if ws<=2 else 75.0 if ws<=5 else 45.0 if ws<=8 else 25.0 if ws<=12 else 10.0
    note = f"wind={ws:.1f}m/s"
    if gust is not None and gust > 8.0: s -= 10.0; note += f", gust={gust:.1f}"
    return max(0.0, s), note

def precip_score(h):
    p = _mm(_get(h,"precipitation.total"))
    if p is None: return 85.0, "precip=unknown"
    if p==0.0: return 100.0, "precip=0"
    if p<=0.05: return 80.0, f"precip={p:.2f}mm"
    if p<=0.2: return 50.0, f"precip={p:.2f}mm"
    return 10.0, f"precip={p:.2f}mm"

def fog_probability(spread_C, wind_ms, vis_km) -> int:
    risk = 0.0
    if spread_C is not None:
        risk += 0.6 if spread_C<=1 else 0.35 if spread_C<=3 else 0.15 if spread_C<=5 else 0.0
    if wind_ms is not None:
        risk += 0.25 if wind_ms<=1.5 else 0.10 if wind_ms<=3.0 else 0.0
    if vis_km is not None:
        risk += 0.25 if vis_km<5 else 0.10 if vis_km<10 else 0.0
    return int(round(min(1.0, risk)*100))

def brightness_model(moon_phase_frac, moon_alt_deg, sep_deg, target_airmass):
    if moon_alt_deg <= 0:
        est_sqm = BASELINE_SQM
    else:
        from math import sin, radians
        f = moon_phase_frac; alt = max(0.0, sin(radians(moon_alt_deg)))
        sep_term = 1.0 if sep_deg is None else 1.0/(1.0 + (sep_deg/40.0)**2)
        X = max(1.0, target_airmass)
        delta_mag = 2.5 * min(1.0, f * alt * sep_term / (X**0.7))
        est_sqm = max(17.0, min(22.0, BASELINE_SQM - delta_mag))
    knots = [(17.5,10),(18.5,35),(19.5,60),(20.5,85),(21.5,100)]
    def interp(xv):
        if xv<=knots[0][0]: return knots[0][1]
        if xv>=knots[-1][0]: return knots[-1][1]
        for (x0,y0),(x1,y1) in zip(knots,knots[1:]):
            if x0<=xv<=x1:
                t=(xv-x0)/(x1-x0); return y0+t*(y1-y0)
        return 60
    score = float(interp(est_sqm))
    return est_sqm, score, f"SQM≈{est_sqm:.2f}"

@dataclass
class NightWindow:
    start: object
    end: object
    label: str

def build_night_windows_from_hourly(hourly_section, geo: Geo) -> List[NightWindow]:
    hours = sorted((hourly_section.data or []), key=lambda h: _get(h, "date"))
    wins: List[NightWindow] = []
    in_dark = False; start_dt = None; prev_dt = None
    for h in hours:
        dt_local = _get(h, "date")
        if not dt_local: continue
        sun, _, _ = geo.compute(_to_utc(dt_local), None)
        sun_alt_deg = float(sun.alt) * 180.0 / 3.141592653589793
        dark = sun_alt_deg <= -18.0
        if dark and not in_dark:
            in_dark = True; start_dt = dt_local
        if in_dark and not dark:
            end_dt = prev_dt or dt_local
            start = start_dt + timedelta(hours=SUNSET_BUFFER_H)
            end   = end_dt   - timedelta(hours=SUNRISE_BUFFER_H)
            if start < end: wins.append(NightWindow(start, end, f"{start.date()} night"))
            in_dark = False; start_dt = None
        prev_dt = dt_local
    if in_dark and start_dt and prev_dt:
        start = start_dt + timedelta(hours=SUNSET_BUFFER_H)
        end   = prev_dt   - timedelta(hours=SUNRISE_BUFFER_H)
        if start < end: wins.append(NightWindow(start, end, f"{start.date()} night"))
    return wins

def hour_quality(h, geo: Geo, target_ra_dec):
    s_clouds, r_clouds = clouds_score(h)
    s_vis,    r_vis    = visibility_score(h)
    s_dew,    r_dew, spread = dewspread_score(h)
    s_wind,   r_wind   = wind_score(h)
    s_precip, r_precip = precip_score(h)
    ws, visk = _ms(_get(h,"wind.speed")), _km(_get(h,"visibility"))
    fogp = fog_probability(spread, ws, visk)

    # Get actual metric values from API
    cloud_total = _pct(_get(h,"cloud_cover.total"))
    precip_total = _mm(_get(h,"precipitation.total"))
    temp = _c(_get(h,"temperature"))
    dewpoint = _c(_get(h,"dew_point")) or _c(_get(h,"dewpoint"))

    dt_local = _get(h,"date"); dt_utc = _to_utc(dt_local)
    sun, moon, star = geo.compute(dt_utc, target_ra_dec)

    if star:
        alt_deg = float(star.alt) * 180.0 / 3.141592653589793
        X = airmass(max(0.0, alt_deg))
    else:
        X = 1.0

    moon_phase_frac = float(moon.phase)/100.0
    moon_alt_deg = float(moon.alt)*180.0/3.141592653589793
    sep_deg = float(ephem.separation(moon, star))*180.0/3.141592653589793 if star else None

    sqm, s_bright, bright_note = brightness_model(moon_phase_frac, moon_alt_deg, sep_deg, X)

    score = (W_CLOUDS*s_clouds + W_VIS*s_vis + W_DEWSPREAD*s_dew +
             W_WIND*s_wind + W_PRECIP*s_precip + W_BRIGHT*s_bright)

    comps = {"clouds":s_clouds,"visibility":s_vis,"dewspread":s_dew,"wind":s_wind,"precip":s_precip,"brightness":s_bright,
             "_fogp":fogp,"_airmass":X,"_sqm":sqm,
             "_cloud_pct":cloud_total,"_precip_mm":precip_total,"_dewspread_c":spread}
    notes = {"clouds":r_clouds,"visibility":r_vis,"dewspread":r_dew,"wind":r_wind,"precip":r_precip,
             "brightness": f"{bright_note}, moon_alt={moon_alt_deg:.0f}°, illum={int(round(moon_phase_frac*100))}%"
                           + (f", sep={sep_deg:.0f}°" if sep_deg is not None else "")}
    return score, comps, notes

def classify(score: float) -> str:
    return "GREAT" if score>=75 else "OK" if score>=60 else "POOR"

def render_html_card(payload: dict) -> str:
    # lightweight, self-contained card
    nights = payload["nights"]
    updated = payload["generated_at_local"]
    
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="../assets/css/dashboard.css">
</head>
<body style="margin:0;padding:16px;background:transparent">
'''
    
    html += f'<div class="astro-card"><div class="astro-h">Whitegate Observatory — Astrophotography Outlook</div>'
    html += f'<div class="astro-sub">Updated {updated}</div>'
    
    # Add table header
    html += '<div class="astro-table">'
    html += '<div class="astro-row astro-header-row">'
    html += '<div>Night</div><div>Score</div><div>Best 2h</div><div>Dewspread</div><div>Clouds</div><div>Precip</div><div>Fog</div><div>SQM</div><div>Airmass</div>'
    html += '</div>'
    
    for n in nights:
        badge = f'<span class="badge {n["class"]}">{n["class"]} {int(round(n["score"]))}</span>'
        best2h_text = n.get("best2h", "—").replace(" (avg ", "<br>(").replace(")", ")") if n.get("best2h") else "—"
        
        html += '<div class="astro-row">'
        html += f'<div><strong>{n["label"]}</strong><br><span class="astro-time">{n["start_local"]}<br/>→ {n["end_local"]}</span></div>'
        html += f'<div>{badge}</div>'
        html += f'<div class="astro-time">{best2h_text}</div>'
        html += f'<div>{n.get("dewspread", "—")}</div>'
        html += f'<div>{n.get("clouds", "—")}</div>'
        html += f'<div>{n.get("precip", "—")}</div>'
        html += f'<div>{n.get("fog", "—")}%</div>'
        html += f'<div>{n.get("sqm", "—")}</div>'
        html += f'<div>{n.get("airmass", "—")}</div>'
        html += '</div>'
    
    html += '</div></div>'
    html += '</body></html>'
    return html

def render_weather_card(location_name: str, hourly_data: list, marine_data: dict = None) -> str:
    """Render a simple 7-day weather forecast card from hourly data.

    marine_data (optional): dict returned by fetch_openmeteo_marine, keyed by
    naive-UTC datetime.  Used to populate the Wave column when Meteosource
    hourly data does not include wave_height.
    """
    # Group by day and get daily summaries
    from collections import defaultdict
    days = defaultdict(list)
    for h in hourly_data[:168]:  # 7 days = 168 hours
        dt = _get(h, "date")
        if not dt: continue
        day_key = dt.strftime("%Y-%m-%d")
        days[day_key].append(h)
    
    body = f'<div class="weather-card-section"><div class="weather-h">{location_name}</div>'
    
    # Header row — includes Wave column
    body += '<div class="day-row header-row" style="grid-template-columns:120px repeat(7,1fr)"><div>Day</div><div class="weather-val">Temp</div><div class="weather-val">Precip</div><div class="weather-val">Wind</div><div class="weather-val">Clouds</div><div class="weather-val">Humidity</div><div class="weather-val">Pressure</div><div class="weather-val">Wave</div></div>'
    
    for day_key in sorted(days.keys())[:7]:
        day_hours = days[day_key]
        if not day_hours: continue
        
        dt = _get(day_hours[0], "date")
        day_label = dt.strftime("%a %d %b") if dt else day_key
        
        temps = [t for t in [_get(h, "temperature") for h in day_hours] if isinstance(t, (int, float))]
        precips = [p if isinstance(p, (int, float)) else 0 for p in [_get(h, "precipitation.total") for h in day_hours]]
        winds = [w if isinstance(w, (int, float)) else 0 for w in [_get(h, "wind.speed") for h in day_hours]]  # m/s from API
        clouds = [c if isinstance(c, (int, float)) else 0 for c in [_get(h, "cloud_cover.total") for h in day_hours]]
        humidity = [hum if isinstance(hum, (int, float)) else 0 for hum in [_get(h, "humidity") for h in day_hours]]
        pressure = [p if isinstance(p, (int, float)) else 0 for p in [_get(h, "pressure") for h in day_hours]]

        # Wave height — prefer Open-Meteo Marine data (Meteosource hourly does
        # not include wave variables in its standard API response)
        if marine_data:
            wave_heights = []
            for h in day_hours:
                h_dt = _get(h, "date")
                if h_dt:
                    h_utc = _to_utc(h_dt)
                    key = datetime(h_utc.year, h_utc.month, h_utc.day, h_utc.hour)
                    wh = marine_data.get(key, {}).get("wave_height")
                    if wh is not None:
                        wave_heights.append(float(wh))
        else:
            wave_heights = [
                v for v in [
                    _get(h, "wave_height") or _get(h, "swell_height")
                    for h in day_hours
                ]
                if isinstance(v, (int, float))
            ]
        
        temp_str = f"{int(min(temps))}°/{int(max(temps))}°C" if temps else "N/A"
        precip_str = f"{sum(precips):.1f}mm" if precips else "0mm"
        wind_str = f"{int(mean(winds) * 3.6 if winds else 0)} km/h"  # Convert m/s to km/h
        cloud_str = f"{int(mean(clouds) if clouds else 0)}%"
        humid_str = f"{int(mean(humidity) if humidity else 0)}%"
        press_str = f"{int(mean(pressure) if pressure else 0)} hPa"
        wave_str = f"{mean(wave_heights):.1f}m" if wave_heights else "N/A"
        
        body += f'<div class="day-row" style="grid-template-columns:120px repeat(7,1fr)"><div>{day_label}</div><div class="weather-val">{temp_str}</div><div class="weather-val">{precip_str}</div><div class="weather-val">{wind_str}</div><div class="weather-val">{cloud_str}</div><div class="weather-val">{humid_str}</div><div class="weather-val">{press_str}</div><div class="weather-val">{wave_str}</div></div>'
    
    body += '</div>'
    return body

def _build_wave_chart_data(marine_data: dict) -> dict:
    """Convert the Open-Meteo marine dict into Chart.js-ready time series.

    marine_data is keyed by naive-UTC datetime (truncated to the hour).
    Returns a dict with:
      labels           – local-time strings (one per hour, 7 days)
      wave_heights     – wave height values in metres (None where missing)
      wave_periods     – wave period values in seconds (None where missing)
      sea_temps        – sea surface temperature in °C (None where missing)
    """
    from zoneinfo import ZoneInfo
    dublin = ZoneInfo("Europe/Dublin")

    sorted_keys = sorted(marine_data.keys())
    labels: List[str] = []
    wave_heights: List = []
    wave_periods: List = []
    sea_temps: List = []

    for k in sorted_keys:
        dt_local = k.replace(tzinfo=timezone.utc).astimezone(dublin)
        labels.append(dt_local.strftime("%a %d %b %H:%M"))
        row = marine_data[k]
        wave_heights.append(round(float(row["wave_height"]), 2) if row.get("wave_height") is not None else None)
        wave_periods.append(round(float(row["wave_period"]), 1) if row.get("wave_period") is not None else None)
        sea_temps.append(round(float(row["sea_surface_temperature"]), 1) if row.get("sea_surface_temperature") is not None else None)

    return {
        "labels":       labels,
        "wave_heights": wave_heights,
        "wave_periods": wave_periods,
        "sea_temps":    sea_temps,
    }


def _render_wave_chart(chart_data: dict) -> str:
    """Return an HTML card containing Chart.js wave height / period / SST charts.

    Renders two stacked charts:
      1. Wave height (m) and sea surface temperature (°C) on a dual-axis chart.
      2. Wave period (s) on a separate chart below.
    Chart.js is loaded from CDN — the same CDN used by the tide chart in fish_build.py.
    """
    chart_json = json.dumps(chart_data)
    return f"""<div style="margin:1rem 0 1.5rem;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">
  <div style="padding:1.5rem;background:#0d3d6b;border-bottom:1px solid #1a5c9e;">
    <h2 style="margin:0 0 0.4rem;color:#fff;font-size:1.5rem;">🌊 Wave &amp; Swell Forecast</h2>
    <p style="margin:0;color:#a8c8e8;font-size:0.92rem;">Cork Harbour entrance &mdash; Open-Meteo Marine data &mdash; 7-day hourly forecast</p>
  </div>
  <div style="padding:16px;">
    <div style="position:relative;width:100%;height:240px;margin-bottom:16px;">
      <canvas id="waveHeightChart"
        aria-label="Wave height and sea surface temperature forecast"
        role="img"></canvas>
    </div>
    <div style="position:relative;width:100%;height:160px;">
      <canvas id="wavePeriodChart"
        aria-label="Wave period forecast in seconds"
        role="img"></canvas>
    </div>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
(function(){{
  var raw = {chart_json};
  if (!raw.labels || !raw.labels.length) return;

  // Show a date label only at midnight local time (label ends "00:00").
  var tickLabels = raw.labels.map(function(lbl) {{
    return lbl.endsWith("00:00") ? lbl.slice(0, 9) : "";
  }});

  var commonScaleX = {{
    ticks: {{
      callback: function(val, idx) {{ return tickLabels[idx]; }},
      maxRotation: 0, autoSkip: false, font: {{ size: 11 }}
    }},
    grid: {{ color: "rgba(0,0,0,0.05)" }}
  }};

  // ── Chart 1: Wave Height + Sea Surface Temperature ──────────────────────────
  new Chart(document.getElementById("waveHeightChart"), {{
    type: "line",
    data: {{
      labels: raw.labels,
      datasets: [
        {{
          label: "Wave Height (m)",
          data: raw.wave_heights,
          borderColor: "#1a90ff",
          backgroundColor: "rgba(26,144,255,0.12)",
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.4,
          yAxisID: "yH",
          spanGaps: true,
        }},
        {{
          label: "Sea Temp (°C)",
          data: raw.sea_temps,
          borderColor: "#ef4444",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          borderDash: [4, 3],
          pointRadius: 0,
          fill: false,
          tension: 0.4,
          yAxisID: "yT",
          spanGaps: true,
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
              var unit = item.dataset.yAxisID === "yT" ? "°C" : "m";
              return item.dataset.label + ": " + v.toFixed(item.dataset.yAxisID === "yT" ? 1 : 2) + unit;
            }}
          }}
        }}
      }},
      scales: {{
        x: commonScaleX,
        yH: {{
          type: "linear", position: "left",
          title: {{ display: true, text: "Wave Height (m)", font: {{ size: 11 }} }},
          ticks: {{ font: {{ size: 11 }} }},
          min: 0,
          grid: {{ color: "rgba(0,0,0,0.05)" }}
        }},
        yT: {{
          type: "linear", position: "right",
          title: {{ display: true, text: "Sea Temp (°C)", font: {{ size: 11 }} }},
          ticks: {{ font: {{ size: 11 }} }},
          grid: {{ drawOnChartArea: false }}
        }}
      }}
    }}
  }});

  // ── Chart 2: Wave Period ─────────────────────────────────────────────────────
  new Chart(document.getElementById("wavePeriodChart"), {{
    type: "line",
    data: {{
      labels: raw.labels,
      datasets: [
        {{
          label: "Wave Period (s)",
          data: raw.wave_periods,
          borderColor: "#8b5cf6",
          backgroundColor: "rgba(139,92,246,0.10)",
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          tension: 0.4,
          spanGaps: true,
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
              return "Wave Period: " + v.toFixed(1) + "s";
            }}
          }}
        }}
      }},
      scales: {{
        x: commonScaleX,
        y: {{
          title: {{ display: true, text: "Period (s)", font: {{ size: 11 }} }},
          ticks: {{ font: {{ size: 11 }} }},
          min: 0,
          grid: {{ color: "rgba(0,0,0,0.05)" }}
        }}
      }}
    }}
  }});
}})();
</script>"""


def render_combined_weather(locations_data: list) -> str:
    """Render combined weather cards for multiple locations."""
    
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="../assets/css/dashboard.css">
</head>
<body style="margin:0;padding:16px;background:transparent">
'''
    
    html += '<div class="weather-container">'
    html += '<div class="weather-grid">'
    
    for item in locations_data:
        if len(item) == 3:
            location_name, hourly_data, marine_data = item
        else:
            location_name, hourly_data = item
            marine_data = None
        html += render_weather_card(location_name, hourly_data, marine_data)
    
    html += '</div></div>'
    html += '</body></html>'
    return html

def main():
    ap = argparse.ArgumentParser(description="Build astro JSON + HTML card.")
    ap.add_argument("--out", default="./astro", help="Output folder served by your website (e.g., /var/www/whitegateobservatory.com/astro)")
    args = ap.parse_args()
    outdir = os.path.abspath(args.out); os.makedirs(outdir, exist_ok=True)

    ms = Meteosource(API_KEY, TIER)
    fc = ms.get_point_forecast(lat=LAT, lon=LON, tz=TZ, lang=langs.ENGLISH, units=units.METRIC,
                               sections=(sections.HOURLY,))
    geo = Geo(LAT, LON, ELEV_M)
    target_ra_dec = (TARGET_RA, TARGET_DEC) if (TARGET_RA and TARGET_DEC) else None
    windows = build_night_windows_from_hourly(fc.hourly, geo)
    hourly = fc.hourly.data or []

    nights_out = []
    for w in windows:
        hrs = [h for h in hourly if _get(h,"date") and w.start <= _get(h,"date") <= w.end]
        if not hrs: continue
        per_hour = []
        for h in hrs:
            s, comps, notes = hour_quality(h, geo, target_ra_dec)
            per_hour.append((h, s, comps, notes))
        night_score = round(mean([x[1] for x in per_hour]), 1)
        klass = classify(night_score)

        comp_names = ["clouds","brightness","dewspread","visibility","wind","precip"]
        comp_avg = {k: mean([x[2][k] for x in per_hour]) for k in comp_names}
        worst3 = ", ".join(f"{k}:{round(v)}" for k,v in sorted(comp_avg.items(), key=lambda kv: kv[1])[:3])

        # best 2h - prioritize post-sunset with zero clouds
        best2h = None; best2h_score = -1
        for i in range(len(per_hour)-1):
            seg = per_hour[i:i+2]
            sc = mean([x[1] for x in seg])
            
            # Calculate bonus for ideal astrophotography conditions
            astro_bonus = 0
            
            # Check if both hours are after sunset
            all_night = True
            for h_data in seg:
                h = h_data[0]
                dt_local = _get(h, "date")
                if dt_local:
                    dt_utc = _to_utc(dt_local)
                    sun_obj, _, _ = geo.compute(dt_utc, None)
                    sun_alt_deg = float(sun_obj.alt) * 180.0 / 3.141592653589793
                    if sun_alt_deg > -6.0:  # not dark enough
                        all_night = False
                        break
            
            if all_night:
                astro_bonus += 20  # Strong bonus for night time
            
            # Check cloud cover - heavily favor zero or near-zero clouds
            avg_cloud = mean([x[2]["clouds"] for x in seg])
            if avg_cloud >= 95:  # essentially zero clouds (score 95-100)
                astro_bonus += 25  # Massive bonus for clear skies
            elif avg_cloud >= 85:
                astro_bonus += 15  # Good bonus for mostly clear
            elif avg_cloud >= 70:
                astro_bonus += 5   # Small bonus for acceptable
            
            # Total adjusted score
            adjusted_sc = sc + astro_bonus
            
            if adjusted_sc > best2h_score:
                best2h_score = adjusted_sc
                t0 = _get(seg[0][0],"date").strftime("%Y-%m-%d %H:%M")
                t1 = _get(seg[-1][0],"date").strftime("%Y-%m-%d %H:%M")
                best2h = f"{t0} → {t1} (avg {sc:.1f})"  # Show original score, not bonus-adjusted

        fog_peak = max(x[2]["_fogp"] for x in per_hour)
        sqm_med  = round(mean(x[2]["_sqm"] for x in per_hour), 2)
        airmass_med = round(mean(x[2]["_airmass"] for x in per_hour), 2)
        
        # Calculate actual metric averages (not scores)
        cloud_pct_vals = [x[2]["_cloud_pct"] for x in per_hour if x[2].get("_cloud_pct") is not None]
        precip_mm_vals = [x[2]["_precip_mm"] for x in per_hour if x[2].get("_precip_mm") is not None]
        dewspread_vals = [x[2]["_dewspread_c"] for x in per_hour if x[2].get("_dewspread_c") is not None]
        
        cloud_pct_avg = round(mean(cloud_pct_vals)) if cloud_pct_vals else None
        precip_mm_avg = round(sum(precip_mm_vals), 1) if precip_mm_vals else None
        dewspread_avg = round(mean(dewspread_vals), 1) if dewspread_vals else None

        nights_out.append({
            "label": w.label,
            "start": w.start.isoformat(),
            "end": w.end.isoformat(),
            "start_local": w.start.strftime("%a %d %b %H:%M"),
            "end_local": w.end.strftime("%a %d %b %H:%M"),
            "score": night_score,
            "class": klass,
            "worst": worst3,
            "best2h": best2h,
            "notes": f"{worst3} • fog≤{fog_peak}%, SQM≈{sqm_med}, airmass≈{airmass_med}",
            "dewspread": f"{dewspread_avg}°C" if dewspread_avg is not None else "—",
            "clouds": f"{cloud_pct_avg}%" if cloud_pct_avg is not None else "—",
            "precip": f"{precip_mm_avg}mm" if precip_mm_avg is not None else "—",
            "fog": fog_peak,
            "sqm": sqm_med,
            "airmass": airmass_med,
        })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_local": datetime.now().strftime("%a %d %b %H:%M"),
        "location": "Whitegate, Co. Cork, IE",
        "nights": sorted(nights_out, key=lambda x: x["score"], reverse=True),
        "baseline_sqm": BASELINE_SQM,
        "target": {"ra": TARGET_RA, "dec": TARGET_DEC} if (TARGET_RA and TARGET_DEC) else None,
    }

    # Write JSON (atomic)
    json_tmp = os.path.join(outdir, "astro.tmp.json")
    json_out = os.path.join(outdir, "astro.json")
    with open(json_tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(json_tmp, json_out)

    # Write HTML card (atomic)
    html_tmp = os.path.join(outdir, "card.tmp.html")
    html_out = os.path.join(outdir, "card.html")
    with open(html_tmp, "w", encoding="utf-8") as f:
        f.write(render_html_card(payload))
    os.replace(html_tmp, html_out)

    print(f"Wrote: {json_out} and {html_out}")
    
    # Generate Cork Harbour weather card (single location replacing Whitegate + Cork City)
    weather_dir = os.path.join(os.path.dirname(outdir), "weather")
    os.makedirs(weather_dir, exist_ok=True)
    
    # Fetch Cork Harbour weather data (centre of the harbour)
    HARBOUR_LAT, HARBOUR_LON = 51.835, -8.28  # Cork Harbour
    fc_harbour = ms.get_point_forecast(lat=HARBOUR_LAT, lon=HARBOUR_LON, tz=TZ, lang=langs.ENGLISH, units=units.METRIC,
                                       sections=(sections.HOURLY,))
    harbour_hourly = fc_harbour.hourly.data or []
    
    # Fetch marine data for the harbour weather card wave column.
    # Import fetch_openmeteo_marine from fish_build (imported below in marine page block).
    import sys
    import re
    sys.path.insert(0, os.path.dirname(__file__))
    from fish_build import (build_payload as build_fishing_payload,
                            render_card as render_fishing_card,
                            fetch_openmeteo_marine)
    # Use a point near the Cork Harbour entrance for representative wave data.
    harbour_marine_data = fetch_openmeteo_marine(51.79, -8.25)

    # Generate weather file for Cork Harbour
    harbour_html_tmp = os.path.join(weather_dir, "forecast.tmp.html")
    harbour_html_out = os.path.join(weather_dir, "forecast.html")
    locations_data = [
        ("Cork Harbour", harbour_hourly, harbour_marine_data)
    ]
    with open(harbour_html_tmp, "w", encoding="utf-8") as f:
        f.write(render_combined_weather(locations_data))
    os.replace(harbour_html_tmp, harbour_html_out)
    
    print(f"Wrote Cork Harbour weather: {harbour_html_out}")
    
    # Generate marine page (weather + fishing + map — astronomy moves to astro-photography page)
    marine_dir = os.path.dirname(outdir)
    marine_html_path = os.path.join(marine_dir, "marine.html")
    marine_tmp_path = os.path.join(marine_dir, "marine.tmp.html")
    
    fishing_payload = build_fishing_payload()
    
    # Extract body content without body tag
    def extract_body_content(html):
        match = re.search(r'<body[^>]*>(.*)</body>', html, re.DOTALL)
        return match.group(1) if match else html
    
    weather_content = extract_body_content(render_combined_weather(locations_data))
    fishing_content = extract_body_content(render_fishing_card(fishing_payload))
    
    wave_chart_data = _build_wave_chart_data(harbour_marine_data)
    wave_chart_content = _render_wave_chart(wave_chart_data)

    # The Meteosource API key is intentionally embedded in the generated HTML so
    # the browser can load weather tile images directly from the Meteosource CDN.
    # This is the standard pattern for all client-side map tile APIs (Mapbox,
    # Google Maps, etc.).  Meteosource keys are registered per domain, which
    # limits unauthorised reuse.  There is no server-side proxy alternative for
    # a fully-static GitHub Pages site.
    ms_api_key_js = API_KEY

    map_section = f'''<!-- Leaflet CSS/JS shared by both maps on this page -->
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<!-- ═══════════════════════════════════════════════════════════════════════
     SAILING WEATHER MAP
     ═══════════════════════════════════════════════════════════════════════ -->
<div style="margin:1rem 0 1.5rem;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">
  <div style="padding:1.5rem;background:#0d3d6b;border-bottom:1px solid #1a5c9e;">
    <h2 style="margin:0 0 0.4rem;color:#fff;font-size:1.5rem;">⛵ Sailing Weather Map</h2>
    <p style="margin:0;color:#a8c8e8;font-size:0.92rem;">Cork Harbour &amp; Coast &mdash; select a layer and step through the forecast</p>
  </div>

  <!-- Layer selector -->
  <div id="wx-layer-bar" style="display:flex;flex-wrap:wrap;gap:6px;padding:12px 16px;background:#f0f4f8;border-bottom:1px solid #dce4ec;">
    <button class="wx-btn wx-active" data-var="wind_speed"        data-label="Wind Speed">💨 Wind Speed</button>
    <button class="wx-btn"           data-var="wind_gust"         data-label="Wind Gusts">💨 Wind Gusts</button>
    <button class="wx-btn"           data-var="wave_height"       data-label="Wave Height">🌊 Wave Height</button>
    <button class="wx-btn"           data-var="wave_period"       data-label="Wave Period">🌊 Wave Period</button>
    <button class="wx-btn"           data-var="humidity"          data-label="Humidity">💧 Humidity</button>
    <button class="wx-btn"           data-var="temperature"       data-label="Temperature">🌡 Temperature</button>
    <button class="wx-btn"           data-var="sea_temperature"   data-label="Sea Temp">🌡 Sea Temp</button>
    <button class="wx-btn"           data-var="precipitation"     data-label="Precipitation">🌧 Precip</button>
    <button class="wx-btn"           data-var="clouds"            data-label="Cloud Cover">☁️ Clouds</button>
    <button class="wx-btn"           data-var="pressure"          data-label="Pressure">🔵 Pressure</button>
  </div>

  <!-- Time slider -->
  <div style="display:flex;align-items:center;gap:12px;padding:10px 16px;background:#f8fafc;border-bottom:1px solid #e5e7eb;">
    <span style="font-size:0.85rem;color:#374151;white-space:nowrap;font-weight:600;">Forecast time:</span>
    <input id="wx-time-slider" type="range" min="0" max="48" step="1" value="0"
           style="flex:1;accent-color:#0d6efd;cursor:pointer;" />
    <span id="wx-time-label" style="font-size:0.85rem;color:#374151;white-space:nowrap;min-width:60px;text-align:right;">Now</span>
    <div style="display:flex;gap:4px;">
      <button id="wx-play-btn" title="Play animation"
              style="padding:3px 10px;font-size:0.8rem;border:1px solid #0d6efd;border-radius:4px;background:#0d6efd;color:#fff;cursor:pointer;">▶ Play</button>
      <button id="wx-stop-btn" title="Stop animation"
              style="padding:3px 10px;font-size:0.8rem;border:1px solid #6c757d;border-radius:4px;background:#6c757d;color:#fff;cursor:pointer;">■ Stop</button>
    </div>
  </div>

  <!-- Map + Legend wrapper -->
  <div style="position:relative;">
    <div id="wx-map" style="height:520px;width:100%;"></div>

    <!-- Active layer label (top-left over map) -->
    <div id="wx-layer-label"
         style="position:absolute;top:10px;left:50px;z-index:900;background:rgba(13,61,107,0.85);
                color:#fff;font-size:0.82rem;font-weight:600;padding:4px 10px;border-radius:4px;pointer-events:none;">
      Wind Speed
    </div>

    <!-- Colour legend (bottom-left over map) -->
    <div id="wx-legend"
         style="position:absolute;bottom:30px;left:10px;z-index:900;background:rgba(255,255,255,0.93);
                border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.18);padding:8px 12px;min-width:160px;font-size:0.78rem;">
      <div id="wx-legend-title" style="font-weight:700;margin-bottom:6px;color:#0d3d6b;">Wind Speed</div>
      <div id="wx-legend-body"></div>
    </div>

    <!-- Wave layer unavailable notice (hidden by default) -->
    <div id="wx-wave-notice"
         style="display:none;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
                z-index:910;background:rgba(13,61,107,0.9);color:#fff;border-radius:8px;
                padding:14px 20px;text-align:center;font-size:0.88rem;max-width:280px;pointer-events:none;">
      🌊 Wave / swell tiles are not available for this API tier or location.
    </div>

    <!-- Sea temperature info notice (hidden by default) -->
    <div id="wx-sst-notice"
         style="display:none;position:absolute;top:10px;right:10px;z-index:910;
                background:rgba(13,61,107,0.85);color:#fff;border-radius:6px;
                padding:6px 12px;font-size:0.78rem;max-width:220px;text-align:center;pointer-events:none;">
      🌡 Sea temperature data covers open ocean only — coastal &amp; land areas show no colour.
    </div>
  </div>

  <div style="padding:8px 16px;background:#f8fafc;border-top:1px solid #e5e7eb;font-size:0.78rem;color:#6b7280;">
    Weather map tiles &copy; <a href="https://www.meteosource.com" target="_blank" rel="noopener" style="color:#0d6efd;">Meteosource</a>
    &nbsp;|&nbsp; Base map &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener" style="color:#0d6efd;">OpenStreetMap</a> contributors
  </div>
</div>

<style>
.wx-btn {{
  padding:5px 12px;font-size:0.82rem;font-weight:500;
  border:1px solid #0d6efd;border-radius:20px;
  background:#fff;color:#0d6efd;cursor:pointer;transition:all 0.15s;
}}
.wx-btn:hover {{ background:#e8f0fe; }}
.wx-btn.wx-active {{ background:#0d6efd;color:#fff; }}
.wx-legend-row {{ display:flex;align-items:center;gap:6px;margin-bottom:3px; }}
.wx-swatch {{ width:18px;height:12px;border-radius:2px;flex-shrink:0; }}
</style>

<script>
(function() {{
  var MS_KEY = "{ms_api_key_js}";
  var MS_TIER = "flexi";

  // ── Layer metadata ──────────────────────────────────────────────────────────
  var LAYERS = {{
    wind_speed:        {{ label:"Wind Speed",     unit:"m/s",  wave:false, legend:[
      {{c:"#0000ff",v:"0"}},{{c:"#00aaff",v:"3"}},{{c:"#00ff88",v:"6"}},
      {{c:"#ffff00",v:"10"}},{{c:"#ff8800",v:"15"}},{{c:"#ff0000",v:"20+"}},
    ]}},
    wind_gust:         {{ label:"Wind Gusts",     unit:"m/s",  wave:false, legend:[
      {{c:"#0000ff",v:"0"}},{{c:"#00aaff",v:"5"}},{{c:"#00ff88",v:"10"}},
      {{c:"#ffff00",v:"15"}},{{c:"#ff8800",v:"20"}},{{c:"#ff0000",v:"25+"}},
    ]}},
    wave_height:       {{ label:"Wave Height",    unit:"m",    wave:true,  legend:[
      {{c:"#b3e0ff",v:"0"}},{{c:"#66b8ff",v:"0.5"}},{{c:"#1a90ff",v:"1"}},
      {{c:"#005eb8",v:"2"}},{{c:"#002080",v:"3+"}},
    ]}},
    wave_period:       {{ label:"Wave Period",    unit:"s",    wave:true,  legend:[
      {{c:"#ffffcc",v:"0"}},{{c:"#a1dab4",v:"5"}},{{c:"#41b6c4",v:"10"}},
      {{c:"#2c7fb8",v:"15"}},{{c:"#253494",v:"20+"}},
    ]}},
    humidity:          {{ label:"Humidity",       unit:"%",    wave:false, legend:[
      {{c:"#f7fbff",v:"0"}},{{c:"#c6dbef",v:"20"}},{{c:"#6baed6",v:"40"}},
      {{c:"#2171b5",v:"60"}},{{c:"#08306b",v:"80+"}},
    ]}},
    temperature:       {{ label:"Temperature",    unit:"°C",   wave:false, legend:[
      {{c:"#0000ff",v:"-10"}},{{c:"#00aaff",v:"0"}},{{c:"#00ff88",v:"10"}},
      {{c:"#ffff00",v:"20"}},{{c:"#ff0000",v:"30+"}},
    ]}},
    sea_temperature:   {{ label:"Sea Temp",       unit:"°C",   wave:false, legend:[
      {{c:"#0000ff",v:"5"}},{{c:"#00aaff",v:"10"}},{{c:"#00ff88",v:"15"}},
      {{c:"#ffff00",v:"20"}},{{c:"#ff0000",v:"25+"}},
    ]}},
    precipitation:     {{ label:"Precipitation",  unit:"mm/h", wave:false, legend:[
      {{c:"#e0f3ff",v:"0"}},{{c:"#74c6ff",v:"1"}},{{c:"#0080ff",v:"3"}},
      {{c:"#004fa3",v:"8"}},{{c:"#800080",v:"15+"}},
    ]}},
    clouds:            {{ label:"Cloud Cover",    unit:"%",    wave:false, legend:[
      {{c:"#ffffff",v:"0"}},{{c:"#cccccc",v:"25"}},{{c:"#999999",v:"50"}},
      {{c:"#555555",v:"75"}},{{c:"#222222",v:"100"}},
    ]}},
    pressure:          {{ label:"Pressure",       unit:"hPa",  wave:false, legend:[
      {{c:"#800000",v:"980"}},{{c:"#ff4400",v:"995"}},{{c:"#ffff00",v:"1010"}},
      {{c:"#00aaff",v:"1020"}},{{c:"#0000aa",v:"1030+"}},
    ]}},
  }};

  // ── Constants ───────────────────────────────────────────────────────────────
  // Zoomed out to show Ireland + surrounding Atlantic so weather gradients are
  // visible across the full tile colour scale (the previous zoom 10 only showed
  // Cork Harbour, too small an area for colour variation to be apparent).
  var MAP_CENTER_LAT  = 52.0;
  var MAP_CENTER_LON  = -8.5;
  var MAP_DEFAULT_ZOOM = 7;
  // Whitegate Observatory coordinates
  var OBS_LAT = 51.825256;
  var OBS_LON = -8.240009;
  // Animation frame interval in milliseconds
  var ANIMATION_INTERVAL_MS = 1800;

  // ── State ───────────────────────────────────────────────────────────────────
  var activeVar   = "wind_speed";
  var forecastHrs = 0;
  var wxLayer     = null;
  var playTimer   = null;

  // ── Map ─────────────────────────────────────────────────────────────────────
  var map = L.map('wx-map').setView([MAP_CENTER_LAT, MAP_CENTER_LON], MAP_DEFAULT_ZOOM);

  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 18,
    opacity: 1,
  }}).addTo(map);

  // Observatory marker
  L.circleMarker([OBS_LAT, OBS_LON], {{
    radius: 7, color: '#1d6fbd', fillColor: '#4a9eff', fillOpacity: 0.9, weight: 2
  }}).addTo(map).bindPopup('<b>Whitegate Observatory</b><br>East Cork, Ireland');

  // ── Tile URL builder ────────────────────────────────────────────────────────
  function tileUrl(varName, hrs) {{
    var dt = hrs === 0 ? '+0hours' : '+' + hrs + 'hours';
    return 'https://www.meteosource.com/api/v1/' + MS_TIER + '/map' +
           '?key=' + MS_KEY +
           '&tile_x={{x}}&tile_y={{y}}&tile_zoom={{z}}' +
           '&variable=' + varName +
           '&datetime=' + dt;
  }}

  // ── Update weather tile layer ───────────────────────────────────────────────
  function updateLayer() {{
    if (wxLayer) {{ map.removeLayer(wxLayer); wxLayer = null; }}
    document.getElementById('wx-wave-notice').style.display = 'none';
    document.getElementById('wx-sst-notice').style.display = 'none';
    if (!MS_KEY || MS_KEY === 'PASTE-YOUR-API-KEY-HERE') {{
      // No key — show a placeholder message and skip tile fetch
      document.getElementById('wx-layer-label').textContent = LAYERS[activeVar].label + ' (API key required)';
      return;
    }}
    // Show sea-temperature info notice (tiles only cover open ocean)
    if (activeVar === 'sea_temperature') {{
      document.getElementById('wx-sst-notice').style.display = 'block';
    }}
    wxLayer = L.tileLayer(tileUrl(activeVar, forecastHrs), {{
      tileSize: 256,
      opacity: 0.75,
      attribution: '&copy; Meteosource',
      maxNativeZoom: 14,
    }});
    // Detect when wave/swell tiles fail to load (404 / no data for this tier).
    // tileTotal is tracked live (incremented on both tileload and tileerror) so
    // that the in-flight tileerror check is meaningful and the notice can appear
    // as soon as more than half the tiles in a batch have errored.
    var isWaveLayer = LAYERS[activeVar] && LAYERS[activeVar].wave;
    if (isWaveLayer) {{
      var errorCount = 0; var loadCount = 0;
      wxLayer.on('loading', function() {{
        errorCount = 0; loadCount = 0;
        document.getElementById('wx-wave-notice').style.display = 'none';
      }});
      wxLayer.on('tileload', function() {{
        loadCount++;
      }});
      wxLayer.on('tileerror', function() {{
        errorCount++;
        var tileTotal = loadCount + errorCount;
        if (tileTotal > 0 && errorCount / tileTotal > 0.5) {{
          document.getElementById('wx-wave-notice').style.display = 'block';
        }}
      }});
      wxLayer.on('load', function() {{
        var tileTotal = loadCount + errorCount;
        if (tileTotal > 0 && errorCount / tileTotal > 0.5) {{
          document.getElementById('wx-wave-notice').style.display = 'block';
        }}
      }});
    }}
    wxLayer.addTo(map);
    document.getElementById('wx-layer-label').textContent = LAYERS[activeVar].label;
    updateLegend();
  }}

  // ── Legend ──────────────────────────────────────────────────────────────────
  function updateLegend() {{
    var meta = LAYERS[activeVar];
    document.getElementById('wx-legend-title').textContent = meta.label + ' (' + meta.unit + ')';
    var html = '';
    meta.legend.forEach(function(row) {{
      html += '<div class="wx-legend-row"><div class="wx-swatch" style="background:' + row.c + '"></div><span>' + row.v + ' ' + meta.unit + '</span></div>';
    }});
    document.getElementById('wx-legend-body').innerHTML = html;
  }}

  // ── Time label ──────────────────────────────────────────────────────────────
  function updateTimeLabel(hrs) {{
    var el = document.getElementById('wx-time-label');
    if (hrs === 0) {{ el.textContent = 'Now'; }}
    else {{ el.textContent = '+' + hrs + 'h'; }}
  }}

  // ── Layer buttons ───────────────────────────────────────────────────────────
  document.querySelectorAll('.wx-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      document.querySelectorAll('.wx-btn').forEach(function(b) {{ b.classList.remove('wx-active'); }});
      btn.classList.add('wx-active');
      activeVar = btn.dataset.var;
      updateLayer();
    }});
  }});

  // ── Time slider ─────────────────────────────────────────────────────────────
  var slider = document.getElementById('wx-time-slider');
  slider.addEventListener('input', function() {{
    forecastHrs = parseInt(this.value);
    updateTimeLabel(forecastHrs);
    updateLayer();
  }});

  // ── Play/Stop animation ─────────────────────────────────────────────────────
  document.getElementById('wx-play-btn').addEventListener('click', function() {{
    if (playTimer) return;
    playTimer = setInterval(function() {{
      forecastHrs = forecastHrs >= 48 ? 0 : forecastHrs + 3;
      slider.value = forecastHrs;
      updateTimeLabel(forecastHrs);
      updateLayer();
    }}, ANIMATION_INTERVAL_MS);
  }});
  document.getElementById('wx-stop-btn').addEventListener('click', function() {{
    if (playTimer) {{ clearInterval(playTimer); playTimer = null; }}
  }});

  // ── Initial render ──────────────────────────────────────────────────────────
  updateLayer();
}})();
</script>

<!-- ═══════════════════════════════════════════════════════════════════════
     FISHING LOCATIONS MAP
     ═══════════════════════════════════════════════════════════════════════ -->
<div style="margin:1rem 0 1.5rem;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">
  <div style="padding:1.5rem;background:#f8f9fa;border-bottom:1px solid #dee2e6;">
    <h2 style="margin:0 0 0.5rem;color:#1a1a1a;font-size:1.5rem;">Fishing Locations</h2>
    <p style="margin:0;color:#6c757d;font-size:0.95rem;">Whitegate, East Cork and Cork Harbour &mdash; click a marker for details</p>
    <p style="margin:0.5rem 0 0;font-size:0.85rem;color:#6c757d;">
      <span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#4a9eff;border:2px solid #1d6fbd;vertical-align:middle;margin-right:4px;"></span>Observatory
      &nbsp;
      <span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#22c55e;border:2px solid #16a34a;vertical-align:middle;margin-right:4px;"></span>Fishing spot
    </p>
  </div>
  <div id="obs-map" style="height:420px;width:100%;"></div>
</div>
<script>
(function() {{
  // Whitegate Observatory coordinates (same as weather map above)
  var OBS_LAT = 51.825256;
  var OBS_LON = -8.240009;

  var map = L.map('obs-map').setView([51.863212, -8.120911], 11);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '\xa9 OpenStreetMap contributors', maxZoom: 19
  }}).addTo(map);

  // Observatory marker
  var obsMarker = L.marker([OBS_LAT, OBS_LON]).addTo(map);
  obsMarker.bindPopup('<b>Whitegate Observatory</b><br>East Cork, Ireland');
  L.circle([OBS_LAT, OBS_LON], {{
    color: '#4a9eff', fillColor: '#4a9eff', fillOpacity: 0.2, radius: 100
  }}).addTo(map);

  // Fishing spots overlay — loaded from external JSON so spots can be updated
  // without modifying any Python build script.  Edit assets/data/fishing-spots.json
  // to add, remove or update spots.
  fetch('assets/data/fishing-spots.json')
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      (data.spots || []).forEach(function(spot) {{
        var catches = (spot.catches || []).join(', ') || '—';
        var seasons = spot.seasons || '—';
        var type    = spot.type    || '';
        var notes   = spot.notes   || '';
        var popup =
          '<b>🎣 ' + spot.name + '</b>' +
          (type    ? '<br><span style="color:#555">Type: </span>'    + type    : '') +
          '<br><span style="color:#555">Fish: </span>'   + catches +
          '<br><span style="color:#555">Best: </span>'   + seasons +
          (notes   ? '<br><span style="color:#555">Notes: </span>'   + notes   : '');
        L.circleMarker([spot.lat, spot.lon], {{
          radius: 8,
          color: '#16a34a',
          fillColor: '#22c55e',
          fillOpacity: 0.85,
          weight: 2
        }}).addTo(map).bindPopup(popup);
      }});
    }})
    .catch(function(e) {{
      console.warn('Could not load fishing spots overlay:', e);
    }});
}})();
</script>'''

    # Build marine page HTML
    updated_time = datetime.now().strftime("%a %d %b %H:%M")
    marine_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Marine - Whitegate Observatory</title>
    <link rel="stylesheet" href="assets/css/dashboard.css">
    <style>
      .top-menu {{
        background: #1a1a1a;
        padding: 0.85rem 2rem;
        border-bottom: 2px solid #333;
      }}
      .top-menu nav {{
        max-width: 1200px;
        margin: 0 auto;
        display: flex;
        gap: 2rem;
        align-items: center;
      }}
      .top-menu a {{
        color: #fff;
        text-decoration: none;
        font-weight: 500;
        padding: 0.5rem 1rem;
        border-radius: 4px;
        transition: background 0.2s;
      }}
      .top-menu a:hover {{
        background: #333;
      }}
      .top-menu .logo {{
        font-size: 1.2rem;
        font-weight: 700;
        color: #4a9eff;
      }}
    </style>
</head>
<body>
<div class="top-menu">
  <nav>
    <a href="index.html" class="logo">Whitegate Observatory</a>
    <a href="marine.html">Marine</a>
    <a href="radio.html">Radio</a>
    <a href="radio-astronomy.html">Radio Astronomy</a>
    <a href="astro-photography.html">Astro Photography</a>
    <a href="tinygs.html">TinyGS</a>
  </nav>
</div>
<div class="update-timestamp">Last updated: {updated_time}<br><span style="font-size: 10px; opacity: 0.8;">Weather data © Meteosource • Tides © WorldTides • Wave/swell data © Open-Meteo</span></div>
{weather_content}
{fishing_content}
<div style="max-width:1200px;margin:0 auto;padding:0 1rem;">
{wave_chart_content}
</div>
{map_section}
</body>
</html>'''
    
    with open(marine_tmp_path, "w", encoding="utf-8") as f:
        f.write(marine_html)
    os.replace(marine_tmp_path, marine_html_path)
    
    print(f"Wrote marine page: {marine_html_path}")

if __name__ == "__main__":
    main()
