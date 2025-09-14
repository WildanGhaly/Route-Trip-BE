import os, math, requests

DEFAULT_SPEED_MPH = 50.0

def _haversine_miles(a: tuple[float,float], b: tuple[float,float]) -> float:
    R_mi = 3958.7613
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R_mi * math.asin(math.sqrt(h))

def _geocode(q: str) -> tuple[float, float] | None:
    url = "https://nominatim.openstreetmap.org/search"
    r = requests.get(url, params={"format": "json", "q": q},
                     headers={"User-Agent": "hos-planner/1.0"}, timeout=15)
    print(f"Nominatim geocode '{q}' status: {r.status_code}")
    if not r.ok: return None
    j = r.json()
    if not j: return None
    return float(j[0]["lat"]), float(j[0]["lon"])

def _directions_ors(api_key: str, start: tuple[float,float], end: tuple[float,float]):
    """
    Call ORS Directions (driving-car). Returns (miles, hours, polyline|None).
    Handles both JSON (routes[]) and GeoJSON (features[]) responses.
    """
    coords = [[start[1], start[0]], [end[1], end[0]]]  # [lon, lat]
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"coordinates": coords}

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    print(f"ORS request -> {url} status={r.status_code}")

    try:
        j = r.json()
    except ValueError:
        print("ORS non-JSON response (trunc):", r.text[:500])
        raise RuntimeError(f"ORS {r.status_code}: non-JSON body")

    if isinstance(j, dict) and "routes" in j and j["routes"]:
        route = j["routes"][0]
        summ = route.get("summary", {})
        distance_m = float(summ["distance"])
        duration_s = float(summ["duration"])
        geom = route.get("geometry")
        poly = geom if isinstance(geom, str) else None
        dist_mi = distance_m * 0.000621371
        dur_hr  = duration_s / 3600.0
        print(f"ORS API call successful (JSON): {dist_mi:.1f} miles, {dur_hr:.2f} hours")
        return dist_mi, dur_hr, poly

    if isinstance(j, dict) and "features" in j and j["features"]:
        feat = j["features"][0]
        summ = feat["properties"]["summary"]
        distance_m = float(summ["distance"])
        duration_s = float(summ["duration"])
        poly = None 
        dist_mi = distance_m * 0.000621371
        dur_hr  = duration_s / 3600.0
        print(f"ORS API call successful (GeoJSON): {dist_mi:.1f} miles, {dur_hr:.2f} hours")
        return dist_mi, dur_hr, poly

    if isinstance(j, dict) and "error" in j:
        print("ORS error object:", j.get("error"))
    else:
        print("ORS unexpected JSON keys:", list(j.keys()) if isinstance(j, dict) else type(j), "body(trunc):", str(j)[:500])
    raise RuntimeError("ORS response missing 'routes'/'features' or invalid format")


def get_route_summary(current_location: str, pickup_location: str, dropoff_location: str, assume_distance_mi: float | None):
    if assume_distance_mi and assume_distance_mi > 0:
        return {
            "distance_mi": float(assume_distance_mi),
            "duration_hr": float(assume_distance_mi / DEFAULT_SPEED_MPH),
            "polyline": None
        }

    api_key = os.getenv("ORS_API_KEY")
    print(f"ORS_API_KEY={'set' if api_key else 'not set'}")
    if api_key:
        pick = _geocode(pickup_location)
        drop = _geocode(dropoff_location)
        print(f"Geocoded: pick={pick}, drop={drop}")
        if pick and drop:
            try:
                dist_mi, dur_hr, poly = _directions_ors(api_key, pick, drop)
                return {"distance_mi": round(dist_mi, 1), "duration_hr": round(dur_hr, 2), "polyline": poly}
            except Exception as e:
                print(f"[route] ORS failed, fallback to haversine. Reason: {e}")
                import math
                def _hav(a: tuple[float,float], b: tuple[float,float]) -> float:
                    R = 3958.7613
                    lat1, lon1 = map(math.radians, a)
                    lat2, lon2 = map(math.radians, b)
                    dlat, dlon = lat2 - lat1, lon2 - lon1
                    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
                    return 2 * R * math.asin(math.sqrt(h))
                dist = _hav(pick, drop)
                return {"distance_mi": round(dist, 1), "duration_hr": round(dist / DEFAULT_SPEED_MPH, 2), "polyline": None}


    # Final fallback (no key or geocode failed)
    dist = 500.0
    return {"distance_mi": dist, "duration_hr": dist / DEFAULT_SPEED_MPH, "polyline": None}
