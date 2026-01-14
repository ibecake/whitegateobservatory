# Whitegate Observatory - Refactoring Plan

## Current Issues
1. No automatic data refresh schedule
2. Mixed responsibilities in build scripts
3. Duplicate code and API calls
4. Hard to maintain and extend

## Proposed Structure

```
whitegateobservatory/
├── .github/workflows/
│   └── update-forecasts.yml          # Scheduled every 4 hours
├── src/
│   ├── __init__.py
│   ├── config.py                      # All configuration in one place
│   ├── api/
│   │   ├── __init__.py
│   │   ├── meteosource.py            # Meteosource API wrapper
│   │   └── worldtides.py             # WorldTides API wrapper
│   ├── processors/
│   │   ├── __init__.py
│   │   ├── astro.py                  # Astronomy calculations
│   │   ├── weather.py                # Weather processing
│   │   └── fishing.py                # Fishing forecast logic
│   ├── renderers/
│   │   ├── __init__.py
│   │   ├── astro_card.py             # HTML for astro
│   │   ├── weather_card.py           # HTML for weather
│   │   └── fishing_card.py           # HTML for fishing
│   └── utils/
│       ├── __init__.py
│       ├── helpers.py                 # Shared utility functions
│       └── cache.py                   # Optional: cache API responses
├── build_site.py                      # Single entry point
├── public/                            # Static HTML pages
│   ├── index.html
│   ├── astro.html
│   ├── whitegate-weather.html
│   ├── cork-weather.html
│   ├── fishing.html
│   └── assets/
└── dist/                              # Generated output (gitignored)
```

## Benefits

### 1. Automatic Updates
- Add cron schedule to run every 4 hours
- Fresh data without manual intervention

### 2. Single Responsibility
- Each module does one thing well
- Easier to test and debug

### 3. Shared Code
- No duplication of helper functions
- Consistent data handling

### 4. Better Configuration
```python
# config.py
class Config:
    METEOSOURCE_API_KEY = os.getenv("METEOSOURCE_API_KEY")
    WORLD_TIDES_KEY = os.getenv("WORLD_TIDES_KEY")
    
    LOCATIONS = {
        "whitegate": {"lat": 51.8268, "lon": -8.2321, "name": "Whitegate, Co. Cork"},
        "cork": {"lat": 51.8985, "lon": -8.4756, "name": "Cork City"}
    }
    
    ASTRO_CONFIG = {
        "baseline_sqm": 20.8,
        "sunset_buffer_h": 1.0,
        # ...
    }
```

### 5. Easier to Extend
- Add new locations by editing config
- Add new forecast types easily
- Plug in different APIs

### 6. Single Build Script
```python
# build_site.py
from src.api.meteosource import fetch_weather
from src.processors.astro import calculate_astro_quality
from src.renderers.astro_card import render_astro_card

def build_all():
    # Fetch data once
    whitegate_data = fetch_weather("whitegate")
    cork_data = fetch_weather("cork")
    
    # Process
    astro_forecast = calculate_astro_quality(whitegate_data)
    
    # Render
    render_astro_card(astro_forecast, "dist/astro")
    # ...
```

## Implementation Priority

### Phase 1: Critical (Do Now)
1. **Add automatic scheduling** - Get fresh data every 4-6 hours
2. **Move to config.py** - Centralize all configuration

### Phase 2: Structure (This Week)
1. Create `src/` directory structure
2. Extract API clients
3. Move HTML rendering to separate files

### Phase 3: Optimization (Later)
1. Add caching layer
2. Add error handling & logging
3. Add tests

## Quick Win: Add Schedule Now

Add this to `.github/workflows/astro.yml`:
```yaml
on:
  push:
    branches: [ main ]
  schedule:
    - cron: '0 */4 * * *'  # Every 4 hours
  workflow_dispatch:
```
