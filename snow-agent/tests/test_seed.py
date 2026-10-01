"""
Smoke tests for resort seed data and the seeding script.

These tests validate the static ``data/resorts.json`` file without touching
any live database, keeping them fast and runnable in any environment.

Requirements: 20.5, 29.1
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate data/resorts.json relative to this file
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RESORTS_JSON = _PROJECT_ROOT / "data" / "resorts.json"

REQUIRED_FIELDS = {
    "name",
    "country",
    "region",
    "latitude",
    "longitude",
    "altitude_base_m",
    "mode",
    "nearest_airports",
}

VALID_MODES = {"sports", "tourism", "both"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def resorts() -> list[dict]:
    """Load and return the full resort list from data/resorts.json."""
    assert _RESORTS_JSON.exists(), (
        f"data/resorts.json not found at {_RESORTS_JSON}. "
        "Run from the project root or check the path."
    )
    with _RESORTS_JSON.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_resorts_json_exists():
    """data/resorts.json must be present in the project."""
    assert _RESORTS_JSON.exists(), f"Missing file: {_RESORTS_JSON}"


def test_total_resort_count(resorts):
    """There must be exactly 70 resort entries in the seed file."""
    assert len(resorts) == 70, (
        f"Expected 70 resorts, found {len(resorts)}"
    )


def test_sports_resort_count(resorts):
    """At least 40 entries must be sports or both mode (Requirement 20.5)."""
    sports = [r for r in resorts if r.get("mode") in ("sports", "both")]
    assert len(sports) >= 40, (
        f"Expected >= 40 sports/both resorts, found {len(sports)}"
    )


def test_tourism_resort_count(resorts):
    """At least 30 entries must be tourism or both mode (Requirement 20.5)."""
    tourism = [r for r in resorts if r.get("mode") in ("tourism", "both")]
    assert len(tourism) >= 30, (
        f"Expected >= 30 tourism/both resorts, found {len(tourism)}"
    )


def test_all_required_fields_present(resorts):
    """Every resort entry must contain all required fields."""
    for resort in resorts:
        missing = REQUIRED_FIELDS - resort.keys()
        assert not missing, (
            f"Resort '{resort.get('name', '<unknown>')}' is missing fields: {missing}"
        )


def test_all_mode_values_are_valid(resorts):
    """Every resort's mode value must be one of 'sports', 'tourism', or 'both'."""
    for resort in resorts:
        mode = resort.get("mode")
        assert mode in VALID_MODES, (
            f"Resort '{resort.get('name')}' has invalid mode: '{mode}'. "
            f"Must be one of {VALID_MODES}."
        )


def test_nearest_airports_are_non_empty_lists(resorts):
    """Every resort must have at least one IATA airport code."""
    for resort in resorts:
        airports = resort.get("nearest_airports")
        assert isinstance(airports, list) and len(airports) >= 1, (
            f"Resort '{resort.get('name')}' has invalid nearest_airports: {airports!r}"
        )


def test_coordinates_are_numeric(resorts):
    """Latitude and longitude must be numeric for every resort."""
    for resort in resorts:
        lat = resort.get("latitude")
        lon = resort.get("longitude")
        assert isinstance(lat, (int, float)), (
            f"Resort '{resort.get('name')}' has non-numeric latitude: {lat!r}"
        )
        assert isinstance(lon, (int, float)), (
            f"Resort '{resort.get('name')}' has non-numeric longitude: {lon!r}"
        )


def test_altitude_base_is_positive_integer(resorts):
    """altitude_base_m must be a positive integer for every resort."""
    for resort in resorts:
        alt = resort.get("altitude_base_m")
        assert isinstance(alt, int) and alt > 0, (
            f"Resort '{resort.get('name')}' has invalid altitude_base_m: {alt!r}"
        )


def test_seed_script_imports_without_error():
    """The seeding script module must be importable without raising any error."""
    # This validates that the module's top-level code (imports, constants) is sound.
    # We do not call main() to avoid triggering a live DB connection.
    spec = importlib.util.find_spec("scripts.seed_resorts")
    assert spec is not None, (
        "scripts.seed_resorts module not found. "
        "Ensure PYTHONPATH includes the project root."
    )
    # Import the module to surface any syntax / import errors
    module = importlib.import_module("scripts.seed_resorts")
    assert hasattr(module, "seed"), "seed() async function not found in seed_resorts module"
    assert hasattr(module, "main"), "main() function not found in seed_resorts module"
