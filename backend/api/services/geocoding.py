"""Geocoding service for Caracas venue resolution.

Primary provider: Google Places API (Text Search) — better venue coverage
for Caracas-specific venues. Fallbacks: Geoapify — free tier, catches edge
cases Google misses; Nominatim (OpenStreetMap) — key-free, always available.

All providers are biased to Caracas bounds; results outside the bounding
box are dropped to avoid wrong-city matches.
"""

import asyncio
import math
import re
import time
from dataclasses import dataclass

import httpx

# Caracas metropolitan bounding box
# Covers Caracas proper + Baruta, Chacao, El Hatillo, Sucre, Vargas/La Guaira,
# San Antonio de los Altos, Guarenas/Guatire. Loose enough to catch edge venues
# without including Maracay/Valencia/other distant cities.
CCS_BOUNDS = {
    "lat_min": 10.30,
    "lat_max": 10.65,
    "lng_min": -67.15,
    "lng_max": -66.65,
}

CCS_CENTER = {"lat": 10.48, "lng": -66.90}

GEOAPIFY_SEARCH_URL = "https://api.geoapify.com/v1/geocode/search"
GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's public instance allows at most 1 request/second per client.
NOMINATIM_USER_AGENT = "Fomoccs/1.0 (Caracas events map)"
NOMINATIM_MIN_INTERVAL_S = 1.1

# Process-wide throttle so concurrent workers share one request/second
# budget instead of each opening their own.
_nominatim_lock = asyncio.Lock()
_nominatim_last_call = 0.0


async def _throttle_nominatim() -> None:
    """Hold the shared Nominatim rate limit."""
    global _nominatim_last_call
    async with _nominatim_lock:
        wait = NOMINATIM_MIN_INTERVAL_S - (time.monotonic() - _nominatim_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _nominatim_last_call = time.monotonic()


@dataclass
class GeocodingResult:
    lat: float
    lng: float
    formatted_address: str
    confidence: float


def normalize_location_name(name: str) -> str:
    """Normalize a location name for dedup matching.

    Ported from pipeline/processor.py::_normalize_location_name.
    """
    if not name:
        return ""
    normalized = re.sub(r"[^\w\s]", "", name.lower())
    return " ".join(normalized.split())


def is_within_caracas(lat: float, lng: float) -> bool:
    """Check if coordinates fall within Caracas bounds."""
    return (
        CCS_BOUNDS["lat_min"] <= lat <= CCS_BOUNDS["lat_max"]
        and CCS_BOUNDS["lng_min"] <= lng <= CCS_BOUNDS["lng_max"]
    )


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance in meters between two lat/lng points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def geocode_location_name(
    name: str,
    api_key: str,
    *,
    address: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> GeocodingResult | None:
    """Forward-geocode a venue name via Geoapify, biased to Caracas.

    Returns None if no result found, API call fails, or result is outside Caracas.
    If *client* is provided it will be reused; otherwise a new one is created.
    """
    search_text = f"{name}, {address}" if address else f"{name}, Caracas"
    params: dict[str, str | int] = {
        "text": search_text,
        "filter": (
            f"rect:{CCS_BOUNDS['lng_min']},{CCS_BOUNDS['lat_min']},"
            f"{CCS_BOUNDS['lng_max']},{CCS_BOUNDS['lat_max']}"
        ),
        "bias": f"proximity:{CCS_CENTER['lng']},{CCS_CENTER['lat']}",
        "type": "amenity",
        "format": "json",
        "limit": 1,
        "apiKey": api_key,
    }

    try:
        if client is not None:
            resp = await client.get(GEOAPIFY_SEARCH_URL, params=params)
            resp.raise_for_status()
        else:
            async with httpx.AsyncClient(timeout=10.0) as _client:
                resp = await _client.get(GEOAPIFY_SEARCH_URL, params=params)
                resp.raise_for_status()
        data: dict[str, object] = resp.json()
    except httpx.HTTPError:
        return None
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None

    hit = results[0]
    if not isinstance(hit, dict):
        return None

    lat = hit.get("lat")
    lon = hit.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None

    if not is_within_caracas(float(lat), float(lon)):
        return None

    rank = hit.get("rank", {})
    confidence = rank.get("confidence", 0.0) if isinstance(rank, dict) else 0.0
    formatted = hit.get("formatted", "")

    return GeocodingResult(
        lat=float(lat),
        lng=float(lon),
        formatted_address=str(formatted),
        confidence=float(confidence),
    )


async def google_places_search(
    name: str,
    google_api_key: str,
    *,
    address: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> GeocodingResult | None:
    """Forward-geocode a venue name via Google Places API (Text Search).

    Uses the new Places API (places.googleapis.com/v1). Returns the first
    result whose location falls within the Caracas bounding box. Result is
    dropped if outside the box (avoids matching the wrong city).

    Returns ``None`` on API error, no result, or out-of-bounds hit.
    """
    text_query = f"{name}, Caracas, Venezuela"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": google_api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.location,places.types"
        ),
    }
    body = {
        "textQuery": text_query,
        "locationBias": {
            "rectangle": {
                "low": {
                    "latitude": CCS_BOUNDS["lat_min"],
                    "longitude": CCS_BOUNDS["lng_min"],
                },
                "high": {
                    "latitude": CCS_BOUNDS["lat_max"],
                    "longitude": CCS_BOUNDS["lng_max"],
                },
            }
        },
        "maxResultCount": 5,
    }

    try:
        if client is not None:
            resp = await client.post(
                GOOGLE_PLACES_SEARCH_URL, headers=headers, json=body, timeout=10.0
            )
        else:
            async with httpx.AsyncClient(timeout=10.0) as _client:
                resp = await _client.post(
                    GOOGLE_PLACES_SEARCH_URL, headers=headers, json=body
                )
        if resp.status_code == 429 or resp.status_code >= 500:
            return None
        resp.raise_for_status()
        data: dict[str, object] = resp.json()
    except httpx.HTTPError:
        return None

    places = data.get("places")
    if not isinstance(places, list) or not places:
        return None

    for place in places:
        if not isinstance(place, dict):
            continue
        loc = place.get("location")
        if not isinstance(loc, dict):
            continue
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if not is_within_caracas(float(lat), float(lng)):
            continue
        formatted = str(place.get("formattedAddress", ""))
        return GeocodingResult(
            lat=float(lat),
            lng=float(lng),
            formatted_address=formatted,
            confidence=1.0,
        )

    return None


async def nominatim_search(
    name: str,
    *,
    address: str | None = None,
    user_agent: str = NOMINATIM_USER_AGENT,
    client: httpx.AsyncClient | None = None,
) -> GeocodingResult | None:
    """Forward-geocode a venue name via OpenStreetMap Nominatim.

    Key-free fallback that keeps working when the paid providers are
    unavailable or out of quota. The query is biased to the Caracas
    bounding box and to Venezuela, and hits outside the box are dropped.

    Returns ``None`` on API error or when nothing resolves in Caracas.
    """
    query = f"{name}, Caracas, Venezuela"
    if address:
        query = f"{name}, {address}, Caracas, Venezuela"

    # viewbox is lon_min,lat_min,lon_max,lat_max — bounded=1 prefers hits inside it.
    viewbox = (
        f"{CCS_BOUNDS['lng_min']},{CCS_BOUNDS['lat_min']},"
        f"{CCS_BOUNDS['lng_max']},{CCS_BOUNDS['lat_max']}"
    )
    params: dict[str, str | int] = {
        "q": query,
        "format": "json",
        "limit": 5,
        "addressdetails": 1,
        "countrycodes": "ve",
        "viewbox": viewbox,
    }

    try:
        headers = {"User-Agent": user_agent, "Accept-Language": "es,en"}
        await _throttle_nominatim()
        if client is not None:
            resp = await client.get(
                NOMINATIM_SEARCH_URL, params=params, headers=headers, timeout=15.0
            )
        else:
            async with httpx.AsyncClient(timeout=15.0) as _client:
                resp = await _client.get(
                    NOMINATIM_SEARCH_URL, params=params, headers=headers, timeout=15.0
                )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    if not isinstance(data, list):
        return None

    for hit in data:
        if not isinstance(hit, dict):
            continue
        # Nominatim serialises lat/lon as strings — coerce before validating.
        try:
            lat = float(hit["lat"])
            lon = float(hit["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not is_within_caracas(lat, lon):
            continue

        importance = hit.get("importance")
        confidence = float(importance) if isinstance(importance, (int, float)) else 0.5
        formatted = hit.get("display_name", "")
        return GeocodingResult(
            lat=lat,
            lng=lon,
            formatted_address=str(formatted),
            confidence=confidence,
        )

    return None


async def geocode_with_fallback(
    name: str,
    *,
    address: str | None = None,
    google_api_key: str | None = None,
    geoapify_api_key: str | None = None,
    nominatim_enabled: bool = True,
    nominatim_user_agent: str = NOMINATIM_USER_AGENT,
    client: httpx.AsyncClient | None = None,
) -> GeocodingResult | None:
    """Try Google Places, then Geoapify, then Nominatim.

    Google Places is preferred because it has better venue coverage for
    Caracas-specific venues. Geoapify is kept as a free safety net, and
    Nominatim guarantees a key-free path so a venue never goes unmapped
    just because every API key is missing, expired or over quota.
    """
    if google_api_key:
        result = await google_places_search(
            name, google_api_key, address=address, client=client
        )
        if result is not None:
            return result
    if geoapify_api_key:
        result = await geocode_location_name(
            name, geoapify_api_key, address=address, client=client
        )
        if result is not None:
            return result
    if nominatim_enabled:
        return await nominatim_search(
            name,
            address=address,
            user_agent=nominatim_user_agent,
            client=client,
        )
    return None
