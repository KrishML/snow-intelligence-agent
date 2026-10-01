import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Import Base and all ORM models so Alembic autogenerate can see them ──────
from app.db.models.base import Base  # noqa: F401
import app.db.models.resort  # noqa: F401
import app.db.models.weekly_run  # noqa: F401
import app.db.models.snow_score  # noqa: F401

# ── Alembic Config object (provides access to values in alembic.ini) ─────────
config = context.config

# ── Override sqlalchemy.url from env var / settings if available ─────────────
try:
    from app.settings import settings  # noqa: E402

    config.set_main_option("sqlalchemy.url", settings.database_url)
except Exception:
    # Fallback: alembic.ini placeholder URL is used (sufficient for generating
    # migration files offline; override at runtime via DATABASE_URL env var)
    pass

# ── Interpret the config file for Python logging ─────────────────────────────
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target metadata for autogenerate support ─────────────────────────────────
target_metadata = Base.metadata


# ── Offline migrations (generate SQL without a live DB connection) ────────────
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Calls to context.execute() emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations (run against a live async DB connection) ────────────────
def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations inside a sync wrapper."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
