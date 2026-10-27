"""Explicit connections and transactional, checksum-checked forward migrations."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

MIGRATIONS = Path(__file__).with_name('migrations')
ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(ValueError):
    pass


class MigrationError(ValueError):
    pass


def load_database_url() -> str:
    """Load the repository root .env without replacing an explicit environment.

    No filesystem search outside this repository and no credential logging.
    This function is called only by commands that actually need a database URL.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        raise ConfigurationError('Install optional dependencies: python -m pip install -r requirements-db.txt') from None
    load_dotenv(dotenv_path=ROOT / '.env', override=False)
    url = os.environ.get('DATABASE_URL', '')
    if not url.strip():
        raise ConfigurationError('Set DATABASE_URL in the environment or repository root .env')
    return url


def connect(url: str | None = None):
    """Never called at import time; no implicit libpq credential fallback."""
    url = url if url is not None else load_database_url()
    if not url.strip():
        raise ConfigurationError('Set DATABASE_URL explicitly before using database commands')
    try:
        import psycopg
    except ImportError:
        raise ConfigurationError('Install optional dependencies: python -m pip install -r requirements-db.txt') from None
    # Disable automatic prepared statements for transaction-pool compatibility.
    return psycopg.connect(url, autocommit=True, connect_timeout=10,
                           application_name='observatorio-ndq', prepare_threshold=None)


def migrate(conn, directory: Path = MIGRATIONS) -> list[str]:
    files = sorted(directory.glob('[0-9][0-9][0-9]_*.sql'))
    if not files or [int(p.name[:3]) for p in files] != list(range(1, len(files) + 1)):
        raise MigrationError('Migrations must be consecutive, starting at 001')
    applied_now = []
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(784331, 1)')
        conn.execute('''CREATE TABLE IF NOT EXISTS public.ndq_schema_migrations (
            name text PRIMARY KEY, sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT current_timestamp)''')
        applied = dict(conn.execute('SELECT name, sha256 FROM public.ndq_schema_migrations').fetchall())
        if set(applied) - {p.name for p in files}:
            raise MigrationError('Database has migrations not present in this checkout')
        if set(applied) != {p.name for p in files[:len(applied)]}:
            raise MigrationError('Applied migrations are not a consecutive prefix')
        for path in files:
            # Git autocrlf must not change migration identity across OSes.
            source = path.read_text(encoding='utf-8')
            checksum = hashlib.sha256(source.encode('utf-8')).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise MigrationError(f'Applied migration was modified: {path.name}')
                continue
            conn.execute(source, prepare=False)
            conn.execute('INSERT INTO public.ndq_schema_migrations (name, sha256) VALUES (%s, %s)',
                         (path.name, checksum))
            applied_now.append(path.name)
    return applied_now
