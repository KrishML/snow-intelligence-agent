"""Read-only Amadeus hotel search normalised to GBP totals; nothing here can book."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MAX_HOTELS = 20
RADIUS_KM = 15
TOP_RESULTS = 5


def _gbp(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def search_hotels(
    client: Any,
    latitude: float,
    longitude: float,
    checkin: date,
    checkout: date,
    max_price_per_night_gbp: Decimal | None = None,
) -> list[dict[str, Any]]:
    """Return up to five GBP-priced offers; offers in other currencies are dropped, not converted."""
    nights = (checkout - checkin).days
    if nights < 1:
        raise ValueError("Checkout must be after check-in.")

    listing = client.reference_data.locations.hotels.by_geocode.get(
        latitude=latitude, longitude=longitude, radius=RADIUS_KM, radiusUnit="KM"
    ).data
    known = {item["hotelId"]: item for item in listing[:MAX_HOTELS] if "hotelId" in item}
    if not known:
        return []

    response = client.shopping.hotel_offers_search.get(
        hotelIds=",".join(known),
        checkInDate=checkin.isoformat(),
        checkOutDate=checkout.isoformat(),
        adults=1,
        currency="GBP",
    )
    searched_at = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    for entry in response.data:
        hotel = entry.get("hotel", {})
        listed = known.get(hotel.get("hotelId"), {})
        priced: list[tuple[Decimal, dict[str, Any]]] = []
        for offer in entry.get("offers", []):
            price = offer.get("price", {})
            total = _gbp(price.get("total"))
            if price.get("currency") == "GBP" and total is not None:
                priced.append((total, offer))
        if not priced:
            continue
        total, offer = min(priced, key=lambda item: item[0])
        nightly = (total / nights).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if max_price_per_night_gbp is not None and nightly > max_price_per_night_gbp:
            continue
        results.append(
            {
                "provider": "amadeus",
                "provider_ref": offer.get("id"),
                "hotel_id": hotel.get("hotelId"),
                "name": hotel.get("name") or listed.get("name"),
                "country_code": (listed.get("address") or {}).get("countryCode"),
                "distance_km": (listed.get("distance") or {}).get("value"),
                "checkin": checkin.isoformat(),
                "checkout": checkout.isoformat(),
                "nights": nights,
                "currency": "GBP",
                "total_gbp": str(total),
                "nightly_avg_gbp": str(nightly),
                "searched_at": searched_at,
            }
        )
    results.sort(key=lambda item: Decimal(item["total_gbp"]))
    return results[:TOP_RESULTS]
