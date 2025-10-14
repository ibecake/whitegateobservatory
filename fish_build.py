#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, json, argparse, time
from typing import Optional, List, Tuple, Dict
from datetime import datetime, timedelta, timezone
from statistics import mean

import requests
import ephem
from zoneinfo import ZoneInfo
from pymeteosource.api import Meteosource
from pymeteosource.types import tiers, sections, langs, units

# ── Config ────────────────────────────────────────────────────────────────────
MS_API_KEY  = os.environ.get("METEOSOURCE_API_KEY", "PASTE-METEOSOURCE-KEY")
MS_TIER     = tiers.FLEXI
LAT, LON    = 51.8268, -8.2321    # Whitegate
TZ          = "Europe/Dublin"
ZLOCAL      = ZoneInfo(TZ)

WT_KEY      = os.environ.get("WORLD_TIDES_KEY")  # WorldTides API key
WT_DAYS     = 7
WT_STEP_S   = 3600  # 1h

# Optional marine overrides until you wire a marine API
OVERRIDE_WAVE_H = os.environ.get("WAVE_H")   # metres
OVERRIDE_WAVE_T = os.environ.get("WAVE_T")   # seconds
OVERRIDE_SEA_T  = os.environ.get("SEA_TEMP") # °C

OUT_DIR   = "dist/fishing"

# ── Card styling (no inner scrollbars; tooltips OK) ───────────────────────────
def shared_card_css() -> str:
    return """
<style>
  html,body{margin:0;padding:0;overflow-x:hidden}

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
  .card{
    width:100%; max-width:none;
    border:1px solid var(--astro-border); border-radius:var(--astro-radius);
    padding:16px; background:var(--astro-bg); box-shadow:var(--astro-shadow); color:var(--astro-fg);
  }
  .h{font-weight:700; font-size:18px; margin:0 0 6px}
  .sub{color:var(--astro-sub); font-size:12px; margin-bottom:12px}
  .credit{margin-top:8px; color:var(--astro-sub); font-size:11px}

  .tblwrap{overflow:visible}
  table{width:100%; border-collapse:collapse; background:var(--astro-bg); color:var(--astro-fg); table-layout:fixed}
  thead th{position:sticky; top:0; background:var(--astro-bg); z-index:1}
  th, td{
    padding:10px; border-top:1px solid var(--astro-border);
    text-align:left; font-size:14px; overflow:visible; white-space:nowrap; vertical-align:middle; text-overflow:ellipsis;
  }
  thead th{border-bottom:1px solid var(--astro-border); color:var(--astro-sub); font-size:12px; letter-spacing:.02em; text-transform:uppercase}
  td.num, th.num{text-align:right}
  .badge{border-radius:999px; padding:2px 8px; font-size:12px; color:#fff; display:inline-block; white-space:nowrap}
  .GOOD{background:var(--badge-good)} .FAIR{background:var(--badge-fair)} .POOR{background:var(--badge-poor)}
  .dim{color:var(--astro-sub)}

  /* info bubble + tooltip */
  td.info{width:2.5rem; text-align:center}
  .tip{position:relative; display:inline-block; cursor:help; outline:none}
  .info-dot{display:inline-block; width:1.35em; height:1.35em; line-height:1.35em; border-radius:999px; background:var(--astro-sub); color:#fff; font-weight:700; font-size:12px; text-align:center}
  .tip-bubble{
    display:none; position:absolute; left:50%; transform:translateX(-50%); bottom:calc(100% + 8px);
    background:var(--astro-fg); color:var(--astro-bg); padding:10px 12px; border-radius:8px;
    box-shadow:0 8px 30px rgba(0,0,0,.25); border:1px solid var(--astro-border);
    max-width:min(80vw, 48ch); white-space:pre-wrap; z-index:20; pointer-events:none;
  }
  .tip:focus .tip-bubble, .tip:hover .tip-bubble{display:block}

  /* Mobile: hide Targets to avoid squish */
  @media (max-width: 640px){
    .card table thead th:nth-child(6),
    .card table tbody td:nth-child(6){ display:none; }
    .badge{ padding:1px 6px; font-size:11px }
    th, td{ padding:6px; font-size:12px; line-height:1.15 }
  }
</style>
"""

def shared_card_js(message_type: str) -> str:
    # NOTE: no buffer, and only post when changed
    return f"""
<script>
(function(){{
  var p = new URLSearchParams(location.search);
  if (p.get("transparent")==="1") document.body.style.background="transparent";

  var lastH = 0;
  function measure(){{
    var b = document.body, d = document.documentElement;
    return Math.max(
      b ? b.scrollHeight : 0,
      d ? d.scrollHeight : 0,
      b ? b.offsetHeight  : 0,
      d ? d.offsetHeight  : 0
    );
  }}
  function postH(){{
    try {{
      var h = measure();
      if (Math.abs(h - lastH) > 1) {{
        parent.postMessage({{type:"{message_type}", height:h}}, "*");
        lastH = h;
      }}
    }} catch(e) {{}}
  }}

  window.addEventListener("load", postH);
  window.addEventListener("resize", postH);
  if ("ResizeObserver" in window) new ResizeObserver(postH).observe(document.body);
  setTimeout(postH,60);
  setTimeout(postH,300);
  setTimeout(postH,1000);
}})();
</script>
"""

# ── Helpers, scoring, tides, build, render  (unchanged from your last version except CSS/JS above)
# ... (for brevity, keep your existing helpers/scoring exactly as before) ...

# Keep your previously working build_payload() and render_card() bodies here
# (no changes needed other than the CSS/JS helpers above).

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # (keep the rest of your existing code for fetching, scoring, grouping by day, and writing files)
    # Reusing your previous implementation…
    from statistics import mean

    # ---- paste your previous build_payload() and render_card() here unchanged ----
    # To keep this message compact, I'm not duplicating the entire functions block.
    # If you'd like, I can paste the whole file again with the identical logic plus these new CSS/JS helpers.

if __name__ == "__main__":
    main()
