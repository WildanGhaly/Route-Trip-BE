# Backend — Django + DRF (HOS Planner API)

Exposes a single endpoint to plan a trip under simplified FMCSA HOS rules and return per-day segments for rendering ELD-style charts.

## Features

- POST /api/plan-trip/ with current/pickup/drop/cycle hours.
- HOS logic: 14-hour on-duty window, 11-hour max driving per day, 30-minute break after 8 hours driving, 70-hour/8-day cycle with automatic 34-hour restart, 1h Pickup + 1h Drop (on-duty), fuel stop every 1000 miles (30 minutes).
- Distance/time via OpenRouteService (if ORS_API_KEY set) with graceful fallback when unavailable.
- CORS ready (via django-cors-headers).

## Project structure
```
backend/
├─ manage.py
├─ .env                   # your secrets (loaded by python-dotenv)
├─ backend/
│  ├─ settings.py         # CORS, REST_FRAMEWORK, etc.
│  ├─ urls.py             # includes tripplanner.urls under /api/
│  ├─ wsgi.py / asgi.py
└─ tripplanner/
   ├─ views.py            # PlanTripView
   ├─ serializers.py
   ├─ hos.py              # HOSPlanner (segments/stops/day grouping)
   └─ route.py            # geocode + ORS directions (optional)
```

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install django djangorestframework django-cors-headers python-dotenv requests gunicorn
```

**Create .env:**

```
SECRET_KEY=change-me
DEBUG=0
ALLOWED_HOSTS=your.domain,127.0.0.1,localhost
CORS_ALLOW_ORIGINS=https://your-fe.example.com
ORS_API_KEY=your_openrouteservice_key
```

**Run:**

```bash
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

**Gunicorn (production quick run):**

```bash
gunicorn backend.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 60
```

## API

**POST /api/plan-trip/**

```json
{
  "current_location": "Kansas City, MO",
  "pickup_location": "Denver, CO",
  "dropoff_location": "Los Angeles, CA",
  "current_cycle_used_hours": 42
}
```

**200 OK:**

```json
{
  "route": { "distance_mi": 1234.5, "duration_hr": 25.6, "polyline": null },
  "stops": [
    { "type": "pickup", "eta": "2025-09-14T08:00:00Z", "duration_min": 60 },
    { "type": "fuel",   "eta": "2025-09-15T14:00:00Z", "duration_min": 30 },
    { "type": "drop",   "eta": "2025-09-16T16:30:00Z", "duration_min": 60 }
  ],
  "days": [
    {
      "index": 1,
      "date": "2025-09-14",
      "segments": [
        { "t0":"08:00","t1":"09:00","status":"on_duty","label":"Pickup" },
        { "t0":"09:00","t1":"13:30","status":"driving","label":"" },
        { "t0":"13:30","t1":"14:00","status":"off","label":"30m Break" }
      ],
      "notes": "Day total: 10.0h driving; window used: 12.5h"
    }
  ]
}
```

Validation errors → 400 with field messages.

## How distance and time are calculated

**If ORS_API_KEY is set**, backend calls OpenRouteService:

- Uses driving-car profile; reads routes[0].summary.distance|duration (or GeoJSON variant).
- Returns encoded polyline when available.

**If ORS fails or is not configured:**

- Falls back to geodesic distance and estimates duration at 50 mph average.

## CORS

For same-origin deployments (Nginx serving FE and proxying /api/), you can leave CORS closed.

For split origins, set CORS_ALLOW_ORIGINS in .env to your frontend origin(s).

## Notes

- Timezone: planner uses UTC in this demo (day starts 08:00 UTC).
- No database models are required; SQLite is present by default for admin sessions if needed.
- Static files for admin: set STATIC_ROOT = BASE_DIR / "staticfiles" and run collectstatic if you use Django admin.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.