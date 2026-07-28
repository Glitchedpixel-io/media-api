# Database permissions for `DATABASE_URL`

This document describes the Postgres privileges the application's runtime
role — the one named by `DATABASE_URL` (or `TEST_DATABASE_URL`) — actually
needs, and gives copy-pasteable `GRANT` statements for setting up a fresh
role. It does **not** cover the role used to run Alembic migrations; see
[Migrations](#migrations-a-separate-role) below.

## What the app does to the database

The app only ever performs row-level reads and writes through SQLAlchemy —
`SELECT` / `INSERT` / `UPDATE` / `DELETE` — against tables in the `public`
schema. It never issues DDL (`CREATE`/`ALTER`/`DROP TABLE`) or `TRUNCATE` at
runtime; schema changes are Alembic's job (see `CLAUDE.md`), driven by a
separate `ALEMBIC_DATABASE_URL`. Every model's primary key is a Postgres
`SERIAL`/identity column, so inserts also need `USAGE` on each table's
backing sequence.

In short, the runtime role needs ordinary DML rights and nothing that lets it
change the shape of the schema.

## Creating the role

Run this once against your target database, as a superuser or the database
owner. Replace `media_api_app` and the password.

```sql
-- 1. Create the login role the app will connect as.
CREATE ROLE media_api_app WITH LOGIN PASSWORD 'change-me';

-- 2. Allow it to connect to the database and use the public schema.
GRANT CONNECT ON DATABASE media_api TO media_api_app;
GRANT USAGE ON SCHEMA public TO media_api_app;

-- 3. DML on every existing table in public.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO media_api_app;

-- 4. USAGE on every existing sequence in public (needed for SERIAL/identity
--    primary keys — INSERT calls nextval() under the hood).
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO media_api_app;

-- 5. Also run this — as whichever role owns the schema's objects (typically
--    the same role Alembic migrates with; see below). It ensures tables and
--    sequences created by *future* migrations are automatically granted to
--    media_api_app, without a manual GRANT after every migration. Skipping
--    this step is what silently broke media_prod_readonly: it had table
--    SELECT but no default-privilege rule for sequences, so every table
--    added after the original grant was reachable but its backing sequence
--    was not, and the gap went unnoticed until pg_dump started failing.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO media_api_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO media_api_app;
```

`ALTER DEFAULT PRIVILEGES` only affects objects created *after* it's run, and
only by the role that ran it — if migrations run under a different role than
the one that set this up, the default-privilege grant is a no-op for objects
that role creates. Set it up (or repeat it) as whatever role actually owns
the created objects.

Then point the app at it:

```
DATABASE_URL=postgresql://media_api_app:change-me@host:5432/media_api
```

## What this role should *not* have

- No `CREATE`/`ALTER`/`DROP` on tables, schemas, or the database.
- No `TRUNCATE` — nothing in `app/` calls it.
- No superuser, `CREATEDB`, `CREATEROLE`, or `BYPASSRLS` attributes.
- No need for privileges on any schema other than `public` — the app never
  sets `search_path` or targets another schema.

## Migrations: a separate role

Schema migrations are handled entirely by Alembic (`alembic/`), using
`ALEMBIC_DATABASE_URL` (falling back to `DATABASE_URL` if unset — see
`CLAUDE.md`). That connection needs full DDL rights (`CREATE`/`ALTER`/`DROP`)
on the schema, which `media_api_app` above deliberately does not have.

For a simple setup, migrations can run as the schema owner. For stricter
object-ownership separation (so application code never runs as the role that
owns the tables), see the `ALEMBIC_OWNER_ROLE` mechanism described in
`CLAUDE.md` under **Database Migrations** — it has migrations `SET ROLE` to a
stable owner role so created objects are consistently owned by it, while the
connecting login role only needs membership in that role.
