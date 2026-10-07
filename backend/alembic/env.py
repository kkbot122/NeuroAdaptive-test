from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, inspect, text
from alembic import context

from app.db.base import Base
from app.core.config import settings

import app.db.model_registry  # noqa: F401 -- shared API/worker/migration metadata

config = context.config

# Use DATABASE_URL from .env
# ConfigParser uses percent interpolation; escape URL-encoded credentials here
# so SQLAlchemy receives the original DATABASE_URL unchanged.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # pgvector halfvec expression index is migration-owned. Its exact
    # presence/definition is asserted in PostgreSQL schema integration.
    return not (type_ == "index" and name == "ix_chunks_embedding_hnsw" and reflected and compare_to is None)



def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # The preserved historical 2f4 revision drops legacy tables. Block a
        # populated pre-2f database rather than silently deleting learner data.
        inspector = inspect(connection)
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar() if inspector.has_table("alembic_version") else None
        if current in (None, "ea9facb393a3"):
            for legacy in ("articles", "paragraphs"):
                if inspector.has_table(legacy) and connection.execute(text(f'SELECT EXISTS (SELECT 1 FROM "{legacy}")')).scalar():
                    raise RuntimeError("Historical revision 2f4c2d25f29c would drop populated legacy tables. Upgrade is blocked; obtain an owner-approved data-preservation path using a clone.")
        # Inspection starts an implicit transaction; end it before Alembic
        # opens its own migration transaction.
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
