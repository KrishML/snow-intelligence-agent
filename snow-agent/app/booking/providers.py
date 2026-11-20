"""Flight, hotel and payment providers for the booking engine.

Each provider is a small protocol so the engine can be tested with fakes and so
a missing integration fails loudly instead of pretending to succeed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.booking.config import BookingSettings


class ProviderError(Exception):
    """A provider call failed; the message is safe to show the user."""


@dataclass(frozen=True)
class Traveller:
    given_name: str
    family_name: str
    born_on: date
    email: str
    phone_number: str
    title: str = "mr"
    gender: str = "m"


@dataclass(frozen=True)
class PlanView:
    plan_id: Any
    user_id: Any
    resort_name: str | None
    outbound_date: date
    return_date: date
    flight_option: dict[str, Any]
    hotel_option: dict[str, Any]
    flight_total: Decimal
    hotel_total: Decimal
    total: Decimal
    created_at: datetime


class FlightProvider(Protocol):
    def blockers(self, plan: PlanView) -> list[str]: ...
    async def hold(self, plan: PlanView, traveller: Traveller) -> str: ...
    async def confirm(self, ref: str, amount: Decimal) -> str: ...
    async def release(self, ref: str) -> None: ...


class HotelProvider(Protocol):
    def blockers(self, plan: PlanView) -> list[str]: ...
    async def hold(self, plan: PlanView, traveller: Traveller) -> str: ...
    async def confirm(self, ref: str, amount: Decimal) -> str: ...
    async def release(self, ref: str) -> None: ...


class PaymentProvider(Protocol):
    async def authorise(
        self, amount: Decimal, payment_method_id: str, idempotency_key: str
    ) -> str: ...
    async def capture(self, ref: str) -> None: ...
    async def cancel(self, ref: str) -> None: ...


_NO_HOTEL_PROVIDER = "Hotel booking is not available: no hotel booking provider is integrated."


class UnconfiguredHotelProvider:
    """No hotel booking integration exists yet; every attempt is refused up front."""

    def blockers(self, plan: PlanView) -> list[str]:
        return [_NO_HOTEL_PROVIDER]

    async def hold(self, plan: PlanView, traveller: Traveller) -> str:
        raise ProviderError(_NO_HOTEL_PROVIDER)

    async def confirm(self, ref: str, amount: Decimal) -> str:
        raise ProviderError(_NO_HOTEL_PROVIDER)

    async def release(self, ref: str) -> None:
        return None


def _pence(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


class DuffelFlightProvider:
    """Duffel hold orders. Only Duffel-sourced offers can be booked here."""

    def __init__(self, settings: BookingSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def blockers(self, plan: PlanView) -> list[str]:
        problems = []
        if not self._settings.duffel_api_key:
            problems.append("Duffel is not configured.")
        offer = plan.flight_option
        if not str(offer.get("duffel_offer_id", "")).startswith("off_"):
            problems.append(
                "This flight was found through a search provider that Duffel cannot book; "
                "it has no Duffel offer ID."
            )
        elif not (offer.get("passengers") or [{}])[0].get("id"):
            problems.append("The Duffel offer has no passenger ID.")
        return problems

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.duffel_base_url,
            timeout=self._settings.provider_timeout_seconds,
            transport=self._transport,
            headers={
                "Authorization": f"Bearer {self._settings.duffel_api_key}",
                "Duffel-Version": "v2",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    async def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            async with self._client() as client:
                response = await client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Flight provider unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"Flight provider returned HTTP {response.status_code}.")
        return response.json().get("data", {})

    async def hold(self, plan: PlanView, traveller: Traveller) -> str:
        offer = plan.flight_option
        data = await self._call(
            "POST",
            "/air/orders",
            {
                "data": {
                    "type": "hold",
                    "selected_offers": [offer["duffel_offer_id"]],
                    "passengers": [
                        {
                            "id": offer["passengers"][0]["id"],
                            "title": traveller.title,
                            "gender": traveller.gender,
                            "given_name": traveller.given_name,
                            "family_name": traveller.family_name,
                            "born_on": traveller.born_on.isoformat(),
                            "email": traveller.email,
                            "phone_number": traveller.phone_number,
                        }
                    ],
                }
            },
        )
        return data["id"]

    async def confirm(self, ref: str, amount: Decimal) -> str:
        await self._call(
            "POST",
            "/air/payments",
            {"data": {"order_id": ref, "payment": {"type": "balance", "currency": "GBP", "amount": f"{amount:.2f}"}}},
        )
        order = await self._call("GET", f"/air/orders/{ref}")
        reference = order.get("booking_reference")
        if not reference:
            raise ProviderError("Flight order was paid but no booking reference was returned.")
        return reference

    async def release(self, ref: str) -> None:
        cancellation = await self._call("POST", "/air/order_cancellations", {"data": {"order_id": ref}})
        await self._call("POST", f"/air/order_cancellations/{cancellation['id']}/actions/confirm")


class StripePaymentProvider:
    """Authorise-then-capture card payments via PaymentIntents (no card data touches us)."""

    def __init__(self, settings: BookingSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def _client(self, idempotency_key: str | None = None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self._settings.stripe_secret_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return httpx.AsyncClient(
            base_url=self._settings.stripe_base_url,
            timeout=self._settings.provider_timeout_seconds,
            transport=self._transport,
            headers=headers,
        )

    async def _post(self, path: str, form: dict[str, str] | None = None, key: str | None = None) -> dict:
        if not self._settings.stripe_secret_key:
            raise ProviderError("Stripe is not configured.")
        try:
            async with self._client(key) as client:
                response = await client.post(path, data=form or {})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Payment provider unreachable: {exc}") from exc
        body = response.json()
        if response.status_code >= 400:
            message = (body.get("error") or {}).get("message", "payment failed")
            raise ProviderError(f"Payment provider declined the request: {message}")
        return body

    async def authorise(self, amount: Decimal, payment_method_id: str, idempotency_key: str) -> str:
        intent = await self._post(
            "/v1/payment_intents",
            {
                "amount": str(_pence(amount)),
                "currency": "gbp",
                "payment_method": payment_method_id,
                "confirm": "true",
                "capture_method": "manual",
                "automatic_payment_methods[enabled]": "true",
                "automatic_payment_methods[allow_redirects]": "never",
            },
            key=idempotency_key,
        )
        if intent.get("status") != "requires_capture":
            # e.g. requires_action (3-D Secure) cannot be completed server-side.
            await self._cancel_quietly(intent.get("id"))
            raise ProviderError(f"Payment was not authorised (status: {intent.get('status')}).")
        return intent["id"]

    async def _cancel_quietly(self, ref: str | None) -> None:
        if ref:
            try:
                await self.cancel(ref)
            except ProviderError:
                pass

    async def capture(self, ref: str) -> None:
        await self._post(f"/v1/payment_intents/{ref}/capture")

    async def cancel(self, ref: str) -> None:
        await self._post(f"/v1/payment_intents/{ref}/cancel")
