"""Residence cities and the origin airports valid for each."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CITY = "London"


@dataclass(frozen=True)
class City:
    name: str
    country: str
    airports: tuple[str, ...]  # first entry is the default origin


_CITIES = (
    City("London", "United Kingdom", ("LHR", "LGW", "STN", "LTN", "LCY")),
    City("Manchester", "United Kingdom", ("MAN",)),
    City("Birmingham", "United Kingdom", ("BHX",)),
    City("Edinburgh", "United Kingdom", ("EDI",)),
    City("Glasgow", "United Kingdom", ("GLA",)),
    City("Bristol", "United Kingdom", ("BRS",)),
)

CITIES: dict[str, City] = {city.name.casefold(): city for city in _CITIES}


def get_city(name: str) -> City | None:
    return CITIES.get(name.strip().casefold())


def list_cities() -> list[City]:
    return list(_CITIES)
