# Whitegate Observatory - Current Architecture

## How It Works Now

### Data Flow
```
1. GitHub Actions runs (manual or on push)
   ↓
2. astro_build.py fetches Meteosource weather data
   ├─→ Calculates astrophotography quality (night windows, SQM, etc.)
   ├─→ Generates dist/astro/card.html (astro forecast)
   └─→ Generates dist/weather/whitegate.html & cork.html (weather cards)
   
3. fish_build.py fetches WorldTides + weather data
   └─→ Generates dist/fishing/card.html (fishing forecast)
   
4. Workflow copies HTML pages & assets to dist/
   ↓
5. GitHub Pages deploys dist/ folder
```

### Current Files

**Build Scripts:**
- `astro_build.py` (400 lines) - Does 3 things:
  1. Fetches weather from Meteosource API
  2. Calculates astronomy quality scores
  3. Generates HTML for both astro AND weather forecasts
  
- `fish_build.py` (447 lines) - Does 3 things:
  1. Fetches tide data from WorldTides API
  2. Calculates fishing conditions
  3. Generates HTML fishing forecast

**Static Pages:**
- `index.html` - Dashboard overview
- `astro.html` - Loads iframe with `dist/astro/astro.html`
- `whitegate-weather.html` - Loads iframe with `dist/weather/whitegate.html`
- `cork-weather.html` - Loads iframe with `dist/weather/cork.html`
- `fishing.html` - Loads iframe with `dist/fishing/card.html`

### Data Refresh Schedule
**Current:** Manual only (on git push) ❌  
**After Fix:** Every 4 hours automatically ✅

## What Each Script Actually Does

### astro_build.py
```python
# Gets weather for Whitegate
weather_data = meteosource.get_point_forecast(lat=51.8268, lon=-8.2321)

# Calculates astro quality
for each_night:
    score = calculate_quality(clouds, visibility, wind, etc.)
    
# Generates 3 HTML files:
# 1. dist/astro/card.html - Astro forecast with night scores
# 2. dist/weather/whitegate.html - 7-day weather for Whitegate
# 3. dist/weather/cork.html - 7-day weather for Cork
```

### fish_build.py
```python
# Gets tide data
tides = worldtides.get_tides(lat, lon)

# Gets weather (same as astro uses)
weather = meteosource.get_point_forecast(lat, lon)

# Calculates fishing conditions
for each_day:
    score = calculate_fishing_quality(tides, moon, weather)
    
# Generates:
# 1. dist/fishing/card.html - Fishing forecast
```

## Why Refactor?

### Current Issues
1. ❌ **Duplication**: Weather fetching happens in both scripts
2. ❌ **Mixed Concerns**: astro_build.py generates weather cards (should be separate)
3. ❌ **No Reuse**: Helper functions duplicated across files
4. ❌ **Hard to Test**: Everything tightly coupled
5. ❌ **Hard to Extend**: Want to add new location? Edit multiple files
6. ❌ **No Error Handling**: If API fails, whole build fails

### After Refactor
1. ✅ **Single API Client**: One place to fetch weather
2. ✅ **Separation**: Each file does ONE thing
3. ✅ **Shared Utilities**: DRY principle
4. ✅ **Testable**: Each module can be tested independently  
5. ✅ **Configurable**: Add locations in config file
6. ✅ **Robust**: Graceful error handling

## Recommendation

**Do the refactor in phases:**

### Week 1: Quick Wins (2 hours)
- ✅ Add automatic schedule (DONE)
- Create `config.py` with all settings
- Add basic error handling

### Week 2: Structure (4-6 hours)
- Create `src/` folder structure
- Extract API clients
- Separate HTML rendering

### Week 3: Polish (2-4 hours)
- Add logging
- Add caching (optional)
- Documentation

**Total Time:** ~10 hours for complete refactor  
**Benefit:** Much easier to maintain and extend going forward
