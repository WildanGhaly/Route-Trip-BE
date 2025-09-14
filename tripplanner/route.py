# tripplanner/route.py
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
    return float(j[0]["lat"]), float(j[0]["lon"])  # (lat, lon)

def _directions_ors(api_key: str, points: list[tuple[float,float]]):
    """
    ORS Directions with waypoints: points=[(lat,lon), ...]
    Returns (miles, hours, polyline|None, seg_minutes:list[int]).
    """
    coords = [[lon, lat] for (lat, lon) in points]  # ORS expects [lon, lat]
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {"Authorization": api_key, "Content-Type": "application/json", "Accept": "application/json"}

    # ❌ no geometry_format — your ORS rejects it. Default response usually includes an encoded 'geometry' string.
    payload = {"coordinates": coords}

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    print(f"ORS request -> {url} status={r.status_code}")

    try:
        j = r.json()
    except ValueError:
        print("ORS non-JSON response (trunc):", r.text[:500])
        raise RuntimeError(f"ORS {r.status_code}: non-JSON body")

    # JSON variant: { routes: [ { summary, segments[], geometry: <encoded string>, ... } ] }
    if isinstance(j, dict) and "routes" in j and j["routes"]:
        route = j["routes"][0]
        summ = route.get("summary", {})
        distance_m = float(summ.get("distance", 0))
        duration_s = float(summ.get("duration", 0))
        seg_minutes = [int(round(float(seg.get("duration", 0)) / 60)) for seg in route.get("segments", [])]
        geom = route.get("geometry")
        poly = geom if isinstance(geom, str) else None
        dist_mi = distance_m * 0.000621371
        dur_hr  = duration_s / 3600.0
        print(f"ORS API call successful (JSON): {dist_mi:.1f} miles, {dur_hr:.2f} hours; polyline? {bool(poly)}; segs={seg_minutes}")
        return dist_mi, dur_hr, poly, seg_minutes

    # GeoJSON variant: { features: [ { properties: { summary, segments[] }, geometry: {coordinates: [...] } } ] }
    if isinstance(j, dict) and "features" in j and j["features"]:
        feat = j["features"][0]
        props = feat.get("properties", {})
        summ = props.get("summary", {})
        distance_m = float(summ.get("distance", 0))
        duration_s = float(summ.get("duration", 0))
        seg_minutes = [int(round(float(seg.get("duration", 0)) / 60)) for seg in props.get("segments", [])]
        poly = None  # no encoded string in this variant
        dist_mi = distance_m * 0.000621371
        dur_hr  = duration_s / 3600.0
        print(f"ORS API call successful (GeoJSON): {dist_mi:.1f} miles, {dur_hr:.2f} hours; polyline? {bool(poly)}; segs={seg_minutes}")
        return dist_mi, dur_hr, poly, seg_minutes

    if isinstance(j, dict) and "error" in j:
        print("ORS error object:", j.get("error"))
    else:
        print("ORS unexpected JSON keys:", list(j.keys()) if isinstance(j, dict) else type(j), "body(trunc):", str(j)[:500])
    raise RuntimeError("ORS response missing 'routes'/'features' or invalid format")


def get_route_summary(current_location: str, pickup_location: str, dropoff_location: str, assume_distance_mi: float | None):
    """
    Computes current -> pickup -> dropoff. Returns total distance/time, optional encoded polyline,
    and 'pre_pickup_min' so the planner can place the Pickup block exactly at the boundary.
    """
    if assume_distance_mi and assume_distance_mi > 0:
        total_min = int(round((assume_distance_mi / DEFAULT_SPEED_MPH) * 60))
        return {
            "distance_mi": float(assume_distance_mi),
            "duration_hr": round(total_min / 60.0, 2),
            "polyline": None,
            "pre_pickup_min": 0,
        }

    api_key = os.getenv("ORS_API_KEY")
    print(f"ORS_API_KEY={'set' if api_key else 'not set'}")

    curr = _geocode(current_location)
    pick = _geocode(pickup_location)
    drop = _geocode(dropoff_location)
    print(f"Geocoded: curr={curr}, pick={pick}, drop={drop}")

    if api_key and curr and pick and drop:
        try:
            dist_mi, dur_hr, poly, seg_minutes = _directions_ors(api_key, [curr, pick, drop])
            pre_pickup_min = int(seg_minutes[0]) if seg_minutes else 0
            return {"distance_mi": round(dist_mi, 1), "duration_hr": round(dur_hr, 2), "polyline": poly, "pre_pickup_min": pre_pickup_min}
        except Exception as e:
            print(f"[route] ORS failed, fallback to haversine. Reason: {e}")

    # Fallbacks (no key / partial geocode)
    total_mi = 0.0
    pre_pickup_min = 0
    if curr and pick:
        leg1 = _haversine_miles(curr, pick)
        total_mi += leg1
        pre_pickup_min = int(round((leg1 / DEFAULT_SPEED_MPH) * 60))
    if pick and drop:
        leg2 = _haversine_miles(pick, drop)
        total_mi += leg2
    if total_mi == 0.0:
        total_mi = 500.0
        pre_pickup_min = 0
    total_min = int(round((total_mi / DEFAULT_SPEED_MPH) * 60))
    return {"distance_mi": round(total_mi, 1), "duration_hr": round(total_min / 60.0, 2), "polyline": None, "pre_pickup_min": pre_pickup_min}
