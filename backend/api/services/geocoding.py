"""Geocoding service for Caracas venue resolution.

Primary provider: Google Places API (Text Search) — better venue coverage
for Caracas-specific venues. Fallback: Geoapify — free tier, catches edge
cases Google misses.

Both providers are biased to Caracas bounds; results outside the bounding
box are dropped to avoid wrong-city matches.
"""

import math
import re
from dataclasses import dataclass

import httpx

# Caracas bounding box
CCS_BOUNDS = {
    "lat_min": 10.35,
    "lat_max": 10.55,
    "lng_min": -67.05,
    "lng_max": -66.75,
}

CCS_CENTER = {"lat": 10.48, "lng": -66.90}

GEOAPIFY_SEARCH_URL = "https://api.geoapify.com/v1/geocode/search"
GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


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


async def geocode_with_fallback(
    name: str,
    *,
    address: str | None = None,
    google_api_key: str | None = None,
    geoapify_api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> GeocodingResult | None:
    """Try Google Places first, fall back to Geoapify, return ``None`` if both fail.

    Google Places is preferred because it has better venue coverage for
    Caracas-specific venues. Geoapify is kept as a free safety net.
    """
    if google_api_key:
        result = await google_places_search(
            name, google_api_key, address=address, client=client
        )
        if result is not None:
            return result
    if geoapify_api_key:
        return await geocode_location_name(
            name, geoapify_api_key, address=address, client=client
        )
    return None
