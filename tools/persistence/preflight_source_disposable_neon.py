"""Read-only 002 preflight for a disposable clone of the development branch.

No write mode is exposed. Do not run this against Neon during offline tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re

from .check_connection import client_tls_details
from .db import MIGRATIONS
from .validate_disposable_neon import _connection, read_config


class SourcePreflightError(ValueError):
    pass


def inspect(conn) -> dict:
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        if conn.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
            raise SourcePreflightError('Read-only transaction unavailable')
        if client_tls_details(conn)['tls_active'] is not True:
            raise SourcePreflightError('Client TLS not confirmed')
        if conn.execute('SELECT current_database()').fetchone()[0] != 'neondb':
            raise SourcePreflightError('Unexpected database')
        schema, ledger = conn.execute("SELECT to_regnamespace('ndq'), to_regclass('public.ndq_schema_migrations')").fetchone()
        if schema is None or ledger is None:
            raise SourcePreflightError('Development-clone foundation is missing')
        applied = dict(conn.execute('SELECT name, sha256 FROM public.ndq_schema_migrations').fetchall())
        foundation = (MIGRATIONS / '001_foundation.sql').read_text(encoding='utf-8')
        if (set(applied) != {'001_foundation.sql'} or
                applied['001_foundation.sql'] != hashlib.sha256(foundation.encode('utf-8')).hexdigest()):
            raise SourcePreflightError('Expected migration 001 only with matching checksum')
        if conn.execute("SELECT to_regclass('ndq.source_snapshots')").fetchone()[0] is not None:
            raise SourcePreflightError('Source tables already exist')
        return {'database': 'neondb', 'tls_active': True,
                'transaction_read_only': True, 'migration_001_verified': True,
                'migration_002_applied': False, 'source_tables_exist': False,
                'branch_id_verified_by_neon_api': False, 'writes_performed': False}


def run() -> dict:
    config = read_config()
    with _connection(config['url'], config['sslmode'], read_only=True) as conn:
        return inspect(conn)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Read-only source migration preflight on a guarded disposable Neon branch')
    parser.parse_args(argv)
    try:
        result = {'success': True, 'mode': 'read_only_preflight', 'target': run()}
        status = 0
    except Exception as exc:
        result = {'success': False, 'mode': 'read_only_preflight',
                  'error_type': type(exc).__name__, 'writes_performed': False,
                  'diagnosis': 'Preflight failed; inspect the disposable branch and local configuration'}
        sqlstate = getattr(exc, 'sqlstate', None)
        if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate):
            result['sqlstate'] = sqlstate
        status = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
