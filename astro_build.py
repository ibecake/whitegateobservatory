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
             "_fogp":fogp,"_airmass":X,"_sqm":sqm}
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

def render_weather_card(location_name: str, hourly_data: list) -> str:
    """Render a simple 7-day weather forecast card from hourly data."""
    # Group by day and get daily summaries
    from collections import defaultdict
    days = defaultdict(list)
    for h in hourly_data[:168]:  # 7 days = 168 hours
        dt = _get(h, "date")
        if not dt: continue
        day_key = dt.strftime("%Y-%m-%d")
        days[day_key].append(h)
    
    body = f'<div class="weather-card-section"><div class="weather-h">{location_name}</div>'
    
    # Header row
    body += '<div class="day-row header-row"><div>Day</div><div class="weather-val">Temp</div><div class="weather-val">Precip</div><div class="weather-val">Wind</div><div class="weather-val">Clouds</div><div class="weather-val">Humidity</div><div class="weather-val">Pressure</div></div>'
    
    for day_key in sorted(days.keys())[:7]:
        day_hours = days[day_key]
        if not day_hours: continue
        
        dt = _get(day_hours[0], "date")
        day_label = dt.strftime("%a %d %b") if dt else day_key
        
        temps = [t for t in [_get(h, "temperature") for h in day_hours] if isinstance(t, (int, float))]
        precips = [p if isinstance(p, (int, float)) else 0 for p in [_get(h, "precipitation.total") for h in day_hours]]
        winds = [w if isinstance(w, (int, float)) else 0 for w in [_get(h, "wind.speed") for h in day_hours]]
        clouds = [c if isinstance(c, (int, float)) else 0 for c in [_get(h, "cloud_cover") for h in day_hours]]
        humidity = [hum if isinstance(hum, (int, float)) else 0 for hum in [_get(h, "humidity") for h in day_hours]]
        pressure = [p if isinstance(p, (int, float)) else 0 for p in [_get(h, "pressure") for h in day_hours]]
        
        temp_str = f"{int(min(temps))}°/{int(max(temps))}°C" if temps else "N/A"
        precip_str = f"{sum(precips):.1f}mm" if precips else "0mm"
        wind_str = f"{int(mean(winds) if winds else 0)} km/h"
        cloud_str = f"{int(mean(clouds) if clouds else 0)}%"
        humid_str = f"{int(mean(humidity) if humidity else 0)}%"
        press_str = f"{int(mean(pressure) if pressure else 0)} hPa"
        
        body += f'<div class="day-row"><div>{day_label}</div><div class="weather-val">{temp_str}</div><div class="weather-val">{precip_str}</div><div class="weather-val">{wind_str}</div><div class="weather-val">{cloud_str}</div><div class="weather-val">{humid_str}</div><div class="weather-val">{press_str}</div></div>'
    
    body += '</div>'
    return body

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
    
    for location_name, hourly_data in locations_data:
        html += render_weather_card(location_name, hourly_data)
    
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
                    sun_obj = geo.sun(dt_utc)
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
            "dewspread": round(comp_avg["dewspread"]),
            "clouds": round(comp_avg["clouds"]),
            "precip": round(comp_avg["precip"]),
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
    
    # Generate combined weather card for both locations
    weather_dir = os.path.join(os.path.dirname(outdir), "weather")
    os.makedirs(weather_dir, exist_ok=True)
    
    # Fetch Cork weather data
    CORK_LAT, CORK_LON = 51.8985, -8.4756  # Cork City
    fc_cork = ms.get_point_forecast(lat=CORK_LAT, lon=CORK_LON, tz=TZ, lang=langs.ENGLISH, units=units.METRIC,
                                     sections=(sections.HOURLY,))
    cork_hourly = fc_cork.hourly.data or []
    
    # Generate single combined weather file with both locations
    combined_html_tmp = os.path.join(weather_dir, "forecast.tmp.html")
    combined_html_out = os.path.join(weather_dir, "forecast.html")
    locations_data = [
        ("Whitegate, Co. Cork", hourly),
        ("Cork City", cork_hourly)
    ]
    with open(combined_html_tmp, "w", encoding="utf-8") as f:
        f.write(render_combined_weather(locations_data))
    os.replace(combined_html_tmp, combined_html_out)
    
    print(f"Wrote combined weather: {combined_html_out}")
    
    # Generate combined page with all three forecasts
    combined_dir = os.path.dirname(outdir)
    combined_html_path = os.path.join(combined_dir, "combined.html")
    combined_tmp_path = os.path.join(combined_dir, "combined.tmp.html")
    
    # Read fishing data
    import sys
    import re
    sys.path.insert(0, os.path.dirname(__file__))
    from fish_build import build_payload as build_fishing_payload, render_card as render_fishing_card
    fishing_payload = build_fishing_payload()
    
    # Extract body content without body tag
    def extract_body_content(html):
        # Remove everything before <body...> and after </body>
        match = re.search(r'<body[^>]*>(.*)</body>', html, re.DOTALL)
        return match.group(1) if match else html
    
    astro_content = extract_body_content(render_html_card(payload))
    weather_content = extract_body_content(render_combined_weather(locations_data))
    fishing_content = extract_body_content(render_fishing_card(fishing_payload))
    
    # Build combined HTML
    updated_time = datetime.now().strftime("%a %d %b %H:%M")
    combined_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Whitegate Observatory</title>
    <link rel="stylesheet" href="assets/css/dashboard.css">
</head>
<body>
<div class="update-timestamp">Last updated: {updated_time}<br><span style="font-size: 10px; opacity: 0.8;">Weather data © Meteosource • Tides © WorldTides</span></div>
{astro_content}
{weather_content}
{fishing_content}
</body>
</html>'''
    
    with open(combined_tmp_path, "w", encoding="utf-8") as f:
        f.write(combined_html)
    os.replace(combined_tmp_path, combined_html_path)
    
    print(f"Wrote combined page: {combined_html_path}")

if __name__ == "__main__":
    main()
