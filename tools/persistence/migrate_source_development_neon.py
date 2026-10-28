"""Guarded migration 002 for the persistent Neon development branch.

Default mode performs read-only checks. This command reads its target only from
the ignored .env.ndq-development file and never ingests source observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .db import MIGRATIONS, ROOT
from .preflight_source_disposable_neon import inspect as inspect_foundation
from .validate_source_disposable_neon import (
    MIGRATION, SOURCE_TABLES, _apply_only_002, _connected_target,
    _migration_checksum, _pilot_state,
)

CONFIG = ROOT / '.env.ndq-development'
SOURCE_FOREIGN_KEYS = 11
SOURCE_CONSTRAINTS = {
    # Per table: primary keys, unique, checks, foreign keys in migration 002.
    'source_snapshots': (1, 2, 7, 0),
    'source_snapshot_items': (1, 0, 10, 1),
    'source_collection_attempts': (1, 4, 11, 2),
    'source_attempt_items': (1, 0, 10, 1),
    'source_change_candidates': (1, 1, 11, 5),
    'source_editorial_proposals': (1, 0, 2, 1),
    'source_editorial_decisions': (1, 0, 3, 1),
}


class DevelopmentSourceError(ValueError):
    pass


def read_config(path: Path = CONFIG) -> dict:
    """Load one named ignored file; no root .env or process DATABASE_URL fallback."""
    from dotenv import dotenv_values

    if not path.is_file():
        raise DevelopmentSourceError('Create the ignored .env.ndq-development file first')
    values = dotenv_values(path, interpolate=False)
    required = ('NDQ_DEVELOPMENT_DATABASE_URL', 'NDQ_DEVELOPMENT_BRANCH_ID',
                'NDQ_DEVELOPMENT_ENDPOINT_ID', 'NDQ_PRODUCTION_BRANCH_ID',
                'NDQ_PRODUCTION_ENDPOINT_ID', 'NDQ_DISPOSABLE_BRANCH_ID',
                'NDQ_DISPOSABLE_ENDPOINT_ID')
    if any(not values.get(key) for key in required):
        raise DevelopmentSourceError('Named development URL and all protected branch/endpoint IDs are required')
    branch_ids = [values[key] for key in ('NDQ_DEVELOPMENT_BRANCH_ID',
                  'NDQ_PRODUCTION_BRANCH_ID', 'NDQ_DISPOSABLE_BRANCH_ID')]
    endpoint_ids = [values[key] for key in ('NDQ_DEVELOPMENT_ENDPOINT_ID',
                    'NDQ_PRODUCTION_ENDPOINT_ID', 'NDQ_DISPOSABLE_ENDPOINT_ID')]
    if (any(not re.fullmatch(r'br-[a-z0-9-]+', value) for value in branch_ids)
            or len(set(branch_ids)) != 3
            or any(not re.fullmatch(r'ep-[a-z0-9-]+', value) for value in endpoint_ids)
            or len(set(endpoint_ids)) != 3):
        raise DevelopmentSourceError('Development and protected branch/endpoint guards are invalid')
    if any(os.environ.get(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS')):
        raise DevelopmentSourceError('Libpq target or session override is present')
    url = values['NDQ_DEVELOPMENT_DATABASE_URL']
    try:
        parsed = urlsplit(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        mode = params.get('sslmode', [])
        host = parsed.hostname.lower() if parsed.hostname else ''
        endpoint = host.split('.')[0] if host.endswith('.neon.tech') else ''
        if (parsed.scheme not in ('postgres', 'postgresql') or endpoint != endpoint_ids[0]
                or endpoint.endswith('-pooler') or parsed.path != '/neondb'
                or parsed.port not in (None, 5432)
                or mode not in (['require'], ['verify-ca'], ['verify-full'])
                or any(key in params for key in ('host', 'hostaddr', 'service',
                                                'options', 'dbname', 'port'))):
            raise DevelopmentSourceError('URL must name the selected direct development endpoint, neondb and explicit TLS')
    except ValueError:
        raise DevelopmentSourceError('Development URL is invalid or has unsupported connection options') from None
    return {'url': url, 'branch_id': branch_ids[0], 'endpoint_id': endpoint_ids[0],
            'sslmode': mode[0]}


def _config_guard(config: dict) -> None:
    # Recheck before connecting in case the process environment changed after
    # read_config(). The actual branch ID is still a manual Neon-console check.
    if any(os.environ.get(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS')):
        raise DevelopmentSourceError('Libpq target or session override is present')


def _connection(config: dict, *, read_only: bool):
    import psycopg

    return psycopg.connect(
        config['url'], sslmode=config['sslmode'],
        options='-c default_transaction_read_only=on' if read_only else '',
        autocommit=True, connect_timeout=10, prepare_threshold=None,
        application_name='observatorio-ndq-development-source-migration')


def _source_tables(conn, *, must_be_absent: bool) -> dict[str, bool]:
    result = {table: conn.execute('SELECT to_regclass(%s)', (f'ndq.{table}',)).fetchone()[0] is not None
              for table in SOURCE_TABLES}
    unexpected = any(result.values()) if must_be_absent else not all(result.values())
    if unexpected:
        raise DevelopmentSourceError('Source table state differs from expected migration state')
    return result


def _preflight(conn, config: dict) -> tuple[dict, tuple]:
    _connected_target(conn, config)  # Effective host, database and client TLS.
    foundation = inspect_foundation(conn)  # Explicit read-only transaction and 001 checksum.
    with conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        _source_tables(conn, must_be_absent=True)
        pilots = _pilot_state(conn)
    return foundation, pilots


def _verify_migrated(conn, before: tuple) -> dict:
    ledger = dict(conn.execute('SELECT name, sha256 FROM public.ndq_schema_migrations').fetchall())
    foundation = (MIGRATIONS / '001_foundation.sql').read_text(encoding='utf-8')
    expected = {'001_foundation.sql': hashlib.sha256(foundation.encode('utf-8')).hexdigest(),
                MIGRATION: _migration_checksum()[1]}
    if ledger != expected:
        raise DevelopmentSourceError('Final migration ledger differs from 001 and 002 checksums')
    _source_tables(conn, must_be_absent=False)
    counts = {table: conn.execute('SELECT count(*) FROM ndq.' + table).fetchone()[0]
              for table in SOURCE_TABLES}
    if any(counts.values()):
        raise DevelopmentSourceError('Source tables are not empty after schema-only migration')
    constraints = conn.execute('''SELECT count(*) FILTER (WHERE contype = 'f'),
        count(*) FILTER (WHERE NOT convalidated)
        FROM pg_constraint WHERE connamespace = 'ndq'::regnamespace''').fetchone()
    source_fks = conn.execute('''SELECT count(*) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'f' AND c.connamespace = 'ndq'::regnamespace
          AND t.relname = ANY(%s)''', (list(SOURCE_TABLES),)).fetchone()[0]
    constraint_rows = conn.execute('''SELECT t.relname,
        count(*) FILTER (WHERE c.contype = 'p'),
        count(*) FILTER (WHERE c.contype = 'u'),
        count(*) FILTER (WHERE c.contype = 'c'),
        count(*) FILTER (WHERE c.contype = 'f')
        FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
        LEFT JOIN pg_constraint c ON c.conrelid = t.oid
        WHERE n.nspname = 'ndq' AND t.relname = ANY(%s)
        GROUP BY t.relname''', (list(SOURCE_TABLES),)).fetchall()
    triggers = conn.execute('''SELECT count(DISTINCT c.relname) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        JOIN pg_namespace pn ON pn.oid = p.pronamespace
        WHERE n.nspname = 'ndq' AND c.relname = ANY(%s)
          AND t.tgname = 'preserve_history' AND NOT t.tgisinternal
          AND t.tgenabled IN ('O', 'A') AND pn.nspname = 'ndq'
          AND p.proname = 'reject_mutation'
          AND (t.tgtype & 58) = 58 AND (t.tgtype & 1) = 0''',
        (list(SOURCE_TABLES),)).fetchone()[0]
    if (constraints != (47, 0) or source_fks != SOURCE_FOREIGN_KEYS
            or {row[0]: tuple(row[1:]) for row in constraint_rows} != SOURCE_CONSTRAINTS
            or triggers != len(SOURCE_TABLES)):
        raise DevelopmentSourceError('Foreign keys, validation or append-only triggers are incomplete')
    if _pilot_state(conn) != before:
        raise DevelopmentSourceError('Pilot identity, revision, review or publication state changed')
    return {'migration_001_checksum_verified': True, 'migration_002_checksum_verified': True,
            'source_tables': len(SOURCE_TABLES), 'source_rows': 0,
            'foreign_keys_total': constraints[0], 'foreign_keys_unvalidated': 0,
            'source_foreign_keys': source_fks, 'append_only_triggers': triggers,
            'pilot_dossiers_unchanged': True, 'editorial_and_publication_unchanged': True}


def run(*, apply: bool = False, confirm_branch_id: str | None = None,
        config_reader=read_config, connection=_connection) -> dict:
    config = config_reader()
    _config_guard(config)
    if apply and (not isinstance(confirm_branch_id, str)
                  or not re.fullmatch(r'br-[a-z0-9-]+', confirm_branch_id)
                  or confirm_branch_id != config['branch_id']):
        raise DevelopmentSourceError('Manual development branch ID confirmation does not match')
    if not apply and confirm_branch_id is not None:
        raise DevelopmentSourceError('Branch confirmation is only used with --apply')
    with connection(config, read_only=True) as conn:
        preflight, before = _preflight(conn, config)
    if not apply:
        return {'mode': 'read_only_preflight', 'preflight': preflight,
                'source_tables_absent': True, 'pilot_dossiers_checked': 3,
                'writes_performed': False, 'branch_id_verified_by_neon_api': False}
    _config_guard(config)
    with connection(config, read_only=False) as conn:
        _, current = _preflight(conn, config)
        if current != before:
            raise DevelopmentSourceError('Pilot state changed before migration')
        # One outer transaction covers migration, ledger and all immediate
        # verification. The reused migration helper takes its advisory lock.
        with conn.transaction():
            _apply_only_002(conn)
            checks = _verify_migrated(conn, before)
    with connection(config, read_only=True) as conn:
        _connected_target(conn, config)
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            checks = _verify_migrated(conn, before)
    return {'mode': 'guarded_development_source_migration',
            'preflight': preflight, 'applied_migrations': [MIGRATION],
            'checks': checks, 'source_observations_ingested': 0,
            'writes_performed': True, 'branch_id_verified_by_neon_api': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Guarded development migration 002; default read-only')
    parser.add_argument('--apply', action='store_true', help='Apply migration 002 after manual branch confirmation')
    parser.add_argument('--confirm-branch-id', help='Development br-... ID checked in the Neon console')
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_branch_id:
        parser.error('--confirm-branch-id is required with --apply')
    if args.confirm_branch_id and not args.apply:
        parser.error('--confirm-branch-id is only used with --apply')
    try:
        result = run(apply=args.apply, confirm_branch_id=args.confirm_branch_id)
        result['success'] = True
        status = 0
    except Exception as exc:
        result = {'success': False,
                  'mode': 'guarded_development_source_migration' if args.apply else 'read_only_preflight',
                  'error_type': type(exc).__name__, 'partial_changes_possible': args.apply,
                  'diagnosis': 'Migration stopped; inspect the selected development branch and local guard configuration'}
        sqlstate = getattr(exc, 'sqlstate', None)
        if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate):
            result['sqlstate'] = sqlstate
        status = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
