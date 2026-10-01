"""
Resort seeding script.

Usage:
    python -m scripts.seed_resorts

Reads data/resorts.json (relative to the project root — the directory that
contains this scripts/ package) and inserts all records into the `resorts`
table.  Idempotency is achieved by loading the set of existing (name, country)
pairs from the database before inserting, and skipping any resort that is
already present.  This avoids the need for a DB-level unique constraint on
those columns.

Exit codes:
    0 — success (N inserted, M skipped)
    1 — unrecoverable error
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.db.models.resort import Resort
from app.db.session import AsyncSessionLocal

# Resolve the project root as the parent of the directory that contains this
# scripts/ package, regardless of the current working directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RESORTS_JSON = _PROJECT_ROOT / "data" / "resorts.json"


async def seed() -> None:
    """Load resorts.json and insert new records into the database."""

    # ── Load JSON ────────────────────────────────────────────────────────────
    if not _RESORTS_JSON.exists():
        print(f"ERROR: data file not found: {_RESORTS_JSON}", file=sys.stderr)
        sys.exit(1)

    with _RESORTS_JSON.open(encoding="utf-8") as fh:
        records: list[dict] = json.load(fh)

    # ── Connect and fetch existing (name, country) pairs ────────────────────
    async with AsyncSessionLocal() as session:
        existing_rows = await session.execute(
            select(Resort.name, Resort.country)
        )
        existing: set[tuple[str, str]] = {
            (row.name, row.country) for row in existing_rows
        }

        # ── Build insert list ────────────────────────────────────────────────
        to_insert: list[Resort] = []
        skipped = 0

        for rec in records:
            key = (rec["name"], rec["country"])
            if key in existing:
                skipped += 1
                continue

            resort = Resort(
                id=uuid.uuid4(),
                name=rec["name"],
                country=rec["country"],
                region=rec.get("region"),
                latitude=rec["latitude"],
                longitude=rec["longitude"],
                altitude_base_m=rec["altitude_base_m"],
                altitude_peak_m=rec.get("altitude_peak_m"),
                mode=rec["mode"],
                nearest_airports=rec["nearest_airports"],
                piste_count=rec.get("piste_count"),
                lift_count=rec.get("lift_count"),
                webcam_url=rec.get("webcam_url"),
                is_active=rec.get("is_active", True),
            )
            to_insert.append(resort)

        # ── Bulk insert ──────────────────────────────────────────────────────
        if to_insert:
            session.add_all(to_insert)
            await session.commit()

    inserted = len(to_insert)
    print(f"Seeded {inserted} new resort(s), skipped {skipped} existing.")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
