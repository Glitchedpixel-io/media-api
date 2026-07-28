import os
import re
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

# A valid unquoted-or-simple SQL identifier for a role name.
_ROLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config


def _database_url() -> str:
    """Resolve the migration connection URL from the environment.

    The URL is intentionally not stored in alembic.ini (it would leak into git
    history). Prefer ALEMBIC_DATABASE_URL so migrations can run under a
    privileged migration role distinct from the app's runtime user, falling
    back to DATABASE_URL.

    Returns:
        str: The SQLAlchemy connection URL.

    Raises:
        RuntimeError: If neither environment variable is set.
    """
    url = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Set ALEMBIC_DATABASE_URL (or DATABASE_URL) to run migrations; "
            "alembic.ini no longer carries a hardcoded connection string."
        )
    return url


def _owner_role() -> str | None:
    """Resolve an optional role to own migration-created objects.

    When ALEMBIC_OWNER_ROLE is set, migrations `SET ROLE` to it so every object
    they create is owned by that stable role rather than by the connecting login
    user (production hardening: the connecting role must be a member of it). When
    unset — the default — no role switch happens and objects are owned by the
    connecting user, which is what a clean dev/CI/contributor database wants.

    Returns:
        str | None: The validated role name, or None if unset.

    Raises:
        RuntimeError: If the configured role name is not a simple SQL identifier.
    """
    role = os.environ.get("ALEMBIC_OWNER_ROLE", "").strip()
    if not role:
        return None
    if not _ROLE_NAME_RE.match(role):
        raise RuntimeError(f"ALEMBIC_OWNER_ROLE is not a valid role identifier: {role!r}")
    return role


# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

from app.database import Base
import app.models

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        owner = _owner_role()
        if owner is not None:
            context.execute(f'SET ROLE "{owner}"')
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section, {})
    # Inject the URL directly rather than via set_main_option so passwords
    # containing '%' are not mangled by ConfigParser interpolation.
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        owner = _owner_role()
        if owner is not None:
            # Objects created below are owned by this role. The connecting login
            # role must be a member of it. Identifier validated in _owner_role().
            connection.execute(text(f'SET ROLE "{owner}"'))
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()
            context.execute(text("COMMIT"))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
