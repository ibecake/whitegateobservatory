#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# astro_build.py  —  Build static JSON + HTML *table* (date-sorted) for your site
#
# What it does
#   • Fetches HOURLY Meteosource data for Whitegate, Co. Cork
#   • Builds *astronomical-night* windows directly from hourly Sun altitude (≤ -18°)
#   • Scores each hour (clouds, visibility, dewspread, wind/gusts, precip, moon brightness)
#   • Aggregates nights, then writes:
#       - <out>/astro.json
#       - <out>/card.html  (table layout with headers, sorted by date)
#
# Usage
#   METEOSOURCE_API_KEY=...  python astro_build.py --out ./astro
#
# Requirements
#   pip install pymeteosource ephem
#
# Notes
#   • The card supports theming via URL params: ?theme=auto|light|dark&transparent=1&radius=12&compact=0
#   • It auto-resizes its height if embedded in an <iframe> (posts message to parent).
#

from __future__ import annotations
import os, json, argparse
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from statistics import mean
from datetime import timedelta, datetime, timezone
from math import sin, cos, radians

import ephem
from pymeteosource.api import Meteosource
from pymeteosource.types import tiers, sections, langs, units

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY  = os.environ.get("METEOSOURCE_API_KEY", "PASTE-YOUR-API-KEY-HERE")
TIER     = tiers.FLEXI
LAT, LON = 51.8268, -8.2321     # Whitegate, Co. Cork
ELEV_M   = 20                   # site elevation (m) — tweak if known
TZ       = "Europe/Dublin"

SUNSET_BUFFER_H  = 1.0          # start scoring 1h after astronomical night begins
SUNRISE_BUFFER_H = 1.0          # stop scoring 1h before it ends

# Optional target (improves brightness model with airmass + moon separation). Leave None if generic.
TARGET_RA  = None               # e.g. "05:35:17"
TARGET_DEC = None               # e.g. "-05:23:28"

# Site baseline darkness (no moon) in mag/arcsec^2 (typical suburban–rural)
BASELINE_SQM = 20.8

# Weights for hourly quality score (0..100)
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

# Airmass (Kasten & Young 1989) for altitude (deg)
def airmass(alt_deg: float) -> float:
    z = max(0.0, 90.0 - alt_deg)
    if z >= 90.0: return 38.0
    return 1.0 / (cos(radians(z)) + 0.50572 * ((96.07995 - z) ** -1.6364))

# ── Geometry via PyEphem ──────────────────────────────────────────────────────
class Geo:
    def __init__(self, lat, lon, elev_m):
        self.obs = ephem.Observer()
        self.obs.lat = str(lat); self.obs.lon = str(lon); self.obs.elevation = elev_m
    def compute(self, dt_utc, target_ra_dec: Optional[Tuple[str,str]]):
        self.obs.date = dt_utc
        moon = ephem.Moon(self.obs)
        sun  = ephem.Sun(self.obs)
        star = None
        if target_ra_dec and target_ra_dec[0] and target_ra_dec[1]:
            star = ephem.FixedBody()
            star._ra  = ephem.hours(target_ra_dec[0])
            star._dec = ephem.degrees(target_ra_dec[1])
            star.compute(self.obs)
        return sun, moon, star

# ── Scores & derived features ─────────────────────────────────────────────────
def clouds_score(h):
    low, mid, high, total = _pct(_get(h,"cloud_cover.low")), _pct(_get(h,"cloud_cover.middle")), _pct(_get(h,"cloud_cover.high")), _pct(_get(h,"cloud_cover.total"))
    if low is not None or mid is not None or high is not None:
        v = (0.6*(low  if low  is not None else (total or 0.0)) +
             0.3*(mid  if mid  is not None else (total or 0.0)) +
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
    if p == 0.0: return 100.0, "precip=0"
    if p <= 0.05: return 80.0, f"precip={p:.2f}mm"
    if p <= 0.2:  return 50.0, f"precip={p:.2f}mm"
    return 10.0, f"precip={p:.2f}mm"

def fog_probability(spread_C, wind_ms, vis_km) -> int:
    risk = 0.0
    if spread_C is not None:
        risk += 0.6 if spread_C <= 1 else 0.35 if spread_C <= 3 else 0.15 if spread_C <= 5 else 0.0
    if wind_ms is not None:
        risk += 0.25 if wind_ms <= 1.5 else 0.10 if wind_ms <= 3.0 else 0.0
    if vis_km is not None:
        risk += 0.25 if vis_km < 5 else 0.10 if vis_km < 10 else 0.0
    return int(round(min(1.0, risk) * 100))

def brightness_model(moon_phase_frac, moon_alt_deg, sep_deg, target_airmass):
    if moon_alt_deg <= 0:
        est_sqm = BASELINE_SQM
    else:
        f   = moon_phase_frac           # 0..1
        alt = max(0.0, sin(radians(moon_alt_deg)))  # 0..1
        sep_term = 1.0 if sep_deg is None else 1.0/(1.0 + (sep_deg/40.0)**2)
        X = max(1.0, target_airmass)
        delta_mag = 2.5 * min(1.0, f * alt * sep_term / (X**0.7))          # cap ~2.5 mag
        est_sqm = max(17.0, min(22.0, BASELINE_SQM - delta_mag))
    # Map SQM to 0..100 quality
    knots = [(17.5,10),(18.5,35),(19.5,60),(20.5,85),(21.5,100)]
    def interp(xv):
        if xv <= knots[0][0]: return knots[0][1]
        if xv >= knots[-1][0]: return knots[-1][1]
        for (x0,y0),(x1,y1) in zip(knots, knots[1:]):
            if x0 <= xv <= x1:
                t = (xv - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return 60
    score = float(interp(est_sqm))
    return est_sqm, score, f"SQM≈{est_sqm:.2f}"

# ── Astronomical-night windows from HOURLY (Sun alt ≤ -18°) ───────────────────
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
            if start < end:
                wins.append(NightWindow(start, end, f"{start.date()} night"))
            in_dark = False; start_dt = None
        prev_dt = dt_local

    # trailing dark at end of series
    if in_dark and start_dt and prev_dt:
        start = start_dt + timedelta(hours=SUNSET_BUFFER_H)
        end   = prev_dt   - timedelta(hours=SUNRISE_BUFFER_H)
        if start < end:
            wins.append(NightWindow(start, end, f"{start.date()} night"))

    return wins

# ── Per-hour quality ──────────────────────────────────────────────────────────
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

# ── HTML card (TABLE layout, sorted by DATE) ──────────────────────────────────
def render_html_card(payload: dict) -> str:
    nights = sorted(payload["nights"], key=lambda n: n["start"])  # ensure date sort
    updated = payload["generated_at_local"]

    css = """
<style>
  :root{
    --astro-font: system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif;
    --astro-bg: #ffffff;
    --astro-fg: #0f172a;
    --astro-sub: #94a3b8;
    --astro-border: #e5e7eb;
    --astro-shadow: 0 2px 10px rgba(0,0,0,.06);
    --astro-accent: #4f46e5;
    --astro-radius: 12px;
    --badge-great: #16a34a; --badge-ok: #ca8a04; --badge-poor: #dc2626;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --astro-bg:#0b1020; --astro-fg:#e5e7eb; --astro-sub:#9aa4b2; --astro-border:#1f2937;
      --astro-shadow: 0 8px 30px rgba(0,0,0,.45);
    }
  }
  .astro-wrap{font-family:var(--astro-font); background:transparent;}
  .astro-card{max-width: 1080px; border:1px solid var(--astro-border); border-radius:var(--astro-radius);
              padding:16px; background:var(--astro-bg); box-shadow:var(--astro-shadow); color:var(--astro-fg);}
  .astro-h{font-weight:700; font-size:18px; margin:0 0 6px}
  .astro-sub{color:var(--astro-sub); font-size:12px; margin-bottom:12px}
  .credit{margin-top:8px; color:var(--astro-sub); font-size:11px}

  .table-wrap{overflow:auto}
  table{width:100%; border-collapse:collapse; min-width: 880px;}
  thead th{position:sticky; top:0; background:var(--astro-bg); z-index:1}
  th, td{padding:10px; border-top:1px solid var(--astro-border); text-align:left; vertical-align:top; font-size:14px}
  thead th{border-bottom:1px solid var(--astro-border); color:var(--astro-sub); font-size:12px; letter-spacing:.02em; text-transform:uppercase}
  td.num, th.num{text-align:right}
  .badge{border-radius:999px; padding:2px 8px; font-size:12px; color:#fff; display:inline-block}
  .GREAT{background:var(--badge-great)} .OK{background:var(--badge-ok)} .POOR{background:var(--badge-poor)}
  .dim{color:var(--astro-sub)}
  /* compact mode */
  .compact .astro-card{padding:12px}
  .compact th, .compact td{padding:8px}
  .compact .astro-h{font-size:16px}
  /* Make Date (col 1), Score (col 4), and Best 2h (col 6) lighter */
  tbody td:nth-child(1),
  tbody td:nth-child(4),
  tbody td:nth-child(6) { color: var(--astro-sub); }

  /* Ensure the bold number in Score also uses the lighter tone */
  tbody td:nth-child(4) strong {
  color: var(--astro-sub);
  font-weight: 600; /* slightly bold, not heavy black */
}

</style>
"""
    # Build table rows
    rows = []
    for n in nights:
        cls = n["class"]
        badge = f'<span class="badge {cls}">{cls}</span>'
        score = f'{int(round(n["score"]))}'
        date_label   = n["label"]      # e.g., "2025-10-13 night"
        start_local  = n["start_local"]
        end_local    = n["end_local"]
        best2h       = n.get("best2h", "—") or "—"
        limits       = n.get("worst", "")
        notes        = n.get("notes", "")
        rows.append(
            "<tr>"
            f"<td>{date_label}</td>"
            f"<td class='dim'>{start_local}</td>"
            f"<td class='dim'>→ {end_local}</td>"
            f"<td class='num'><strong>{score}</strong></td>"
            f"<td>{badge}</td>"
            f"<td>{best2h}</td>"
            f"<td class='dim'>{limits}</td>"
            f"<td class='dim'>{notes}</td>"
            "</tr>"
        )

    # Client-side theming & auto-resize
    js = """
<script>
(function(){
  var p = new URLSearchParams(location.search);
  var theme = p.get("theme");
  if (theme === "light") { document.documentElement.classList.remove("dark"); }
  else if (theme === "dark") { document.documentElement.classList.add("dark"); }
  var acc = p.get("accent"); if (acc) { document.documentElement.style.setProperty("--astro-accent", acc); }
  if (p.get("useAccentBadges") === "1") {
    var accVal = getComputedStyle(document.documentElement).getPropertyValue("--astro-accent");
    document.documentElement.style.setProperty("--badge-great", accVal);
    document.documentElement.style.setProperty("--badge-ok", accVal);
    document.documentElement.style.setProperty("--badge-poor", accVal);
  }
  var r = p.get("radius"); if (r) { document.documentElement.style.setProperty("--astro-radius", r.endsWith("px")?r:(r+'px')); }
  var f = p.get("font"); if (f) { document.documentElement.style.setProperty("--astro-font", f); }
  if (p.get("compact") === "1") { document.getElementById("astro-root").classList.add("compact"); }
  if (p.get("transparent") === "1") { document.body.style.background = "transparent"; }

  function send(){ try { parent.postMessage({type:"astro-card-size", height: document.documentElement.scrollHeight}, "*"); } catch(e){} }
  window.addEventListener("load", send); setTimeout(send, 60); setTimeout(send, 300);
})();
</script>
"""
    html = (
        css +
        '<div id="astro-root" class="astro-wrap"><div class="astro-card">'
        '<div class="astro-h">Whitegate Observatory — Astrophotography Outlook</div>'
        f'<div class="astro-sub">Updated {updated}</div>'
        '<div class="table-wrap"><table>'
        '<thead><tr>'
        '<th>Date</th><th>Start</th><th>End</th><th class="num">Score</th><th>Class</th><th>Best 2h</th><th>Limits</th><th>Notes</th>'
        '</tr></thead><tbody>' +
        "".join(rows) +
        '</tbody></table></div>'
        '<div class="credit">Weather data © Meteosource</div>'
        '</div></div>' +
        js
    )
    return html

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Build astro JSON + HTML card (table, date-sorted).")
    ap.add_argument("--out", default="./astro", help="Output folder (served by your website or Pages)")
    args = ap.parse_args()
    outdir = os.path.abspath(args.out); os.makedirs(outdir, exist_ok=True)

    ms = Meteosource(API_KEY, TIER)
    fc = ms.get_point_forecast(
        lat=LAT, lon=LON, tz=TZ, lang=langs.ENGLISH, units=units.METRIC,
        sections=(sections.HOURLY,)
    )

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

        # Limiting factors (avg component scores -> lowest three)
        comp_names = ["clouds","brightness","dewspread","visibility","wind","precip"]
        comp_avg = {k: mean([x[2][k] for x in per_hour]) for k in comp_names}
        worst3 = ", ".join(f"{k}:{round(v)}" for k,v in sorted(comp_avg.items(), key=lambda kv: kv[1])[:3])

        # best 2h (rolling)
        best2h = None; best2h_score = -1
        for i in range(len(per_hour)-1):
            seg = per_hour[i:i+2]
            sc = mean([x[1] for x in seg])
            if sc > best2h_score:
                best2h_score = sc
                t0 = _get(seg[0][0],"date").strftime("%Y-%m-%d %H:%M")
                t1 = _get(seg[-1][0],"date").strftime("%Y-%m-%d %H:%M")
                best2h = f"{t0} → {t1} (avg {best2h_score:.1f})"

        fog_peak   = max(x[2]["_fogp"] for x in per_hour)
        sqm_med    = round(mean(x[2]["_sqm"] for x in per_hour), 2)
        airmass_md = round(mean(x[2]["_airmass"] for x in per_hour), 2)

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
            "notes": f"{worst3} • fog≤{fog_peak}%, SQM≈{sqm_med}, airmass≈{airmass_md}",
        })

    # Payload (DATE-SORTED here)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_local": datetime.now().strftime("%a %d %b %H:%M"),
        "location": "Whitegate, Co. Cork, IE",
        "nights": sorted(nights_out, key=lambda x: x["start"]),  # <<< sort by date
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

if __name__ == "__main__":
    main()
