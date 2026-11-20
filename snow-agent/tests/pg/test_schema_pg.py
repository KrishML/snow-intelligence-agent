from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.booking.models import BookingBase
from app.db.models import Base
from tests.factories import make_resort

pytestmark = pytest.mark.pg

HEAD = "0006"
EXPECTED_TABLES = {
    "resorts", "weekly_runs", "snow_scores", "qualitative_signals", "webcam_snapshots",
    "trip_plans", "user_prefs", "preference_embeddings", "trip_feedback_embeddings",
    "resort_knowledge_embeddings", "session_summary_embeddings", "consent_log",
    "bookings", "booking_events", "alembic_version",
}


async def _seed_plan(factory) -> uuid.UUID:
    from app.db.models.trip_plan import TripPlan

    resort = make_resort(name=f"Resort {uuid.uuid4()}")
    plan = TripPlan(
        id=uuid.uuid4(), user_id=uuid.uuid4(), session_id="s", resort_id=resort.id,
        outbound_date=date(2026, 12, 4), return_date=date(2026, 12, 6),
        flight_option={"total_gbp": "1"}, hotel_option={"total_gbp": "2"}, status="draft",
    )
    async with factory() as session:
        session.add_all([resort, plan])
        await session.commit()
    return plan.id


async def _insert_consent(session, plan_id: uuid.UUID, channel: str = "web") -> uuid.UUID:
    consent_id = uuid.uuid4()
    await session.execute(
        text(
            """INSERT INTO consent_log (id, plan_id, user_id, channel, flight_total_gbp,
                   hotel_total_gbp, total_gbp, price_lock_expires_at, plan_snapshot)
               VALUES (:id, :plan, :user, :channel, 1, 2, 3, now(), CAST('{}' AS jsonb))"""
        ),
        {"id": consent_id, "plan": plan_id, "user": uuid.uuid4(), "channel": channel},
    )
    return consent_id


async def _insert_booking(session, plan_id, consent_id, status="consented") -> uuid.UUID:
    booking_id = uuid.uuid4()
    await session.execute(
        text(
            """INSERT INTO bookings (id, plan_id, user_id, consent_id, status)
               VALUES (:id, :plan, :user, :consent, :status)"""
        ),
        {"id": booking_id, "plan": plan_id, "user": uuid.uuid4(), "consent": consent_id, "status": status},
    )
    return booking_id


async def test_migrations_reach_head_and_create_every_table(factory):
    async with factory() as session:
        version = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        tables = {
            row[0]
            for row in await session.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
    assert version == HEAD
    assert EXPECTED_TABLES <= tables


async def test_orm_models_match_migrated_columns(factory):
    """A model column missing from (or extra to) the migrations fails here, not in production."""
    async with factory() as session:
        connection = await session.connection()
        mismatches = {}
        for metadata in (Base.metadata, BookingBase.metadata):
            for table in metadata.tables.values():
                db_columns = await connection.run_sync(
                    lambda sync, name=table.name: {c["name"] for c in inspect(sync).get_columns(name)}
                )
                orm_columns = {c.name for c in table.columns}
                if db_columns != orm_columns:
                    mismatches[table.name] = {
                        "orm_only": sorted(orm_columns - db_columns),
                        "db_only": sorted(db_columns - orm_columns),
                    }
    assert mismatches == {}


async def test_orm_datetime_columns_match_migrated_timezone_awareness(factory):
    """asyncpg rejects an aware datetime for a naive column, so the two must agree."""
    from sqlalchemy import DateTime

    async with factory() as session:
        connection = await session.connection()
        mismatches = []
        for metadata in (Base.metadata, BookingBase.metadata):
            for table in metadata.tables.values():
                db_columns = await connection.run_sync(
                    lambda sync, name=table.name: {c["name"]: c["type"] for c in inspect(sync).get_columns(name)}
                )
                for column in table.columns:
                    db_type = db_columns.get(column.name)
                    if isinstance(column.type, DateTime) and getattr(db_type, "timezone", None) is not None:
                        if bool(column.type.timezone) != bool(db_type.timezone):
                            mismatches.append(
                                f"{table.name}.{column.name}: model tz={bool(column.type.timezone)}, "
                                f"database tz={bool(db_type.timezone)}"
                            )
    assert mismatches == []


async def test_array_and_jsonb_columns_round_trip(factory):
    from app.db.models.weekly_run import WeeklyRun

    resort = make_resort(name=f"Resort {uuid.uuid4()}")
    run = WeeklyRun(
        id=uuid.uuid4(), run_date=date(2026, 11, 20), mode="sports", status="complete",
        error_log={"reddit:x": "rate limited", "nested": {"n": [1, 2]}},
    )
    async with factory() as session:
        session.add_all([resort, run])
        await session.commit()
    async with factory() as session:
        stored_resort = await session.get(type(resort), resort.id)
        stored_run = await session.get(WeeklyRun, run.id)
    assert stored_resort.nearest_airports == ["INN", "SZG"]
    assert stored_run.error_log == {"reddit:x": "rate limited", "nested": {"n": [1, 2]}}


# --- consent_log is append-only ---------------------------------------------------------


async def test_consent_log_accepts_inserts_but_rejects_update_delete_and_truncate(factory):
    plan_id = await _seed_plan(factory)
    async with factory() as session:
        consent_id = await _insert_consent(session, plan_id)
        await session.commit()

    for statement in (
        "UPDATE consent_log SET total_gbp = 999 WHERE id = :id",
        "DELETE FROM consent_log WHERE id = :id",
    ):
        async with factory() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(text(statement), {"id": consent_id})
    async with factory() as session:
        with pytest.raises(DBAPIError, match="foreign key|append-only"):
            await session.execute(text("TRUNCATE consent_log"))
    # CASCADE gets past the foreign-key check, so this one must be stopped by the trigger.
    async with factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("TRUNCATE consent_log CASCADE"))

    async with factory() as session:
        total = (
            await session.execute(text("SELECT total_gbp FROM consent_log WHERE id = :id"), {"id": consent_id})
        ).scalar()
    assert total == Decimal("3.00")


async def test_consent_log_rejects_unknown_channel(factory):
    plan_id = await _seed_plan(factory)
    async with factory() as session:
        with pytest.raises(IntegrityError, match="ck_consent_log_channel"):
            await _insert_consent(session, plan_id, channel="carrier-pigeon")


# --- bookings -----------------------------------------------------------------------------


async def test_only_one_open_booking_per_plan(factory):
    plan_id = await _seed_plan(factory)
    async with factory() as session:
        consent_id = await _insert_consent(session, plan_id)
        first = await _insert_booking(session, plan_id, consent_id)
        await session.commit()

    async with factory() as session:
        with pytest.raises(IntegrityError, match="uq_bookings_open_plan"):
            await _insert_booking(session, plan_id, consent_id)

    # A cleanly failed attempt frees the plan.
    async with factory() as session:
        await session.execute(text("UPDATE bookings SET status = 'failed' WHERE id = :id"), {"id": first})
        await _insert_booking(session, plan_id, consent_id)
        await session.commit()

    # failed_manual_review does not: money may still be involved.
    other_plan = await _seed_plan(factory)
    async with factory() as session:
        consent_id = await _insert_consent(session, other_plan)
        await _insert_booking(session, other_plan, consent_id, status="failed_manual_review")
        await session.commit()
    async with factory() as session:
        with pytest.raises(IntegrityError, match="uq_bookings_open_plan"):
            await _insert_booking(session, other_plan, consent_id)


async def test_booking_requires_existing_plan_consent_and_valid_status(factory):
    plan_id = await _seed_plan(factory)
    async with factory() as session:
        consent_id = await _insert_consent(session, plan_id)
        await session.commit()

    async with factory() as session:
        with pytest.raises(IntegrityError, match="fk_bookings_plan_id"):
            await _insert_booking(session, uuid.uuid4(), consent_id)
    async with factory() as session:
        with pytest.raises(IntegrityError, match="fk_bookings_consent_id"):
            await _insert_booking(session, plan_id, uuid.uuid4())
    async with factory() as session:
        with pytest.raises(IntegrityError, match="ck_bookings_status"):
            await _insert_booking(session, plan_id, consent_id, status="teleported")


async def test_consent_log_rejects_plan_that_does_not_exist(factory):
    async with factory() as session:
        with pytest.raises(IntegrityError, match="fk_consent_log_plan_id"):
            await _insert_consent(session, uuid.uuid4())


async def test_aware_datetimes_are_accepted_for_timestamptz_columns(factory):
    """The booking engine writes timezone-aware times into the consent record."""
    from app.booking.models import ConsentLog

    plan_id = await _seed_plan(factory)
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    consent = ConsentLog(
        id=uuid.uuid4(), plan_id=plan_id, user_id=uuid.uuid4(), channel="web",
        flight_total_gbp=Decimal("1"), hotel_total_gbp=Decimal("2"), total_gbp=Decimal("3"),
        price_lock_expires_at=expires, plan_snapshot={"ok": True},
    )
    async with factory() as session:
        session.add(consent)
        await session.commit()
    async with factory() as session:
        stored = await session.get(ConsentLog, consent.id)
    assert stored.price_lock_expires_at == expires
    assert stored.consented_at is not None
