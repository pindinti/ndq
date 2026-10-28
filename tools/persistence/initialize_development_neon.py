"""Guarded initialization of the persistent Neon development branch.

Preflight is read-only. --apply is reserved for explicit authorization and a
manual branch-ID confirmation. Neither mode reads the disposable database URL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .check_connection import client_tls_details
from .contract import load_pilots
from .db import ROOT, migrate
from .repository import import_pilots
from .validate_disposable_neon import _counts, _verify


class TargetError(ValueError):
    pass


def read_config(root: Path = ROOT) -> dict:
    """Use only the repository's development URL and a credential-free guard file."""
    from dotenv import dotenv_values

    guard_file = root / '.env.ndq-development'
    dev_file = root / '.env'
    if not guard_file.is_file() or not dev_file.is_file():
        raise TargetError('Development guard file and root .env are required')
    guard = dotenv_values(guard_file, interpolate=False)
    keys = ('NDQ_DEVELOPMENT_BRANCH_ID', 'NDQ_DEVELOPMENT_ENDPOINT_ID',
            'NDQ_PRODUCTION_BRANCH_ID', 'NDQ_PRODUCTION_ENDPOINT_ID',
            'NDQ_DISPOSABLE_BRANCH_ID', 'NDQ_DISPOSABLE_ENDPOINT_ID')
    if any(not guard.get(key) for key in keys):
        raise TargetError('All development, production and disposable branch/endpoint IDs are required')
    dev_branch, prod_branch, disposable_branch = (guard[key] for key in
        ('NDQ_DEVELOPMENT_BRANCH_ID', 'NDQ_PRODUCTION_BRANCH_ID', 'NDQ_DISPOSABLE_BRANCH_ID'))
    dev_endpoint, prod_endpoint, disposable_endpoint = (guard[key] for key in
        ('NDQ_DEVELOPMENT_ENDPOINT_ID', 'NDQ_PRODUCTION_ENDPOINT_ID', 'NDQ_DISPOSABLE_ENDPOINT_ID'))
    if any(not re.fullmatch(r'br-[a-z0-9-]+', value) for value in
           (dev_branch, prod_branch, disposable_branch)):
        raise TargetError('Invalid branch ID format')
    if any(not re.fullmatch(r'ep-[a-z0-9-]+', value) for value in
           (dev_endpoint, prod_endpoint, disposable_endpoint)):
        raise TargetError('Invalid endpoint ID format')
    if len({dev_branch, prod_branch, disposable_branch}) != 3:
        raise TargetError('Development branch ID matches a protected branch')
    if len({dev_endpoint, prod_endpoint, disposable_endpoint}) != 3:
        raise TargetError('Development endpoint ID matches a protected endpoint')
    url = dotenv_values(dev_file, interpolate=False).get('DATABASE_URL', '')
    if not url:
        raise TargetError('Root .env must contain the persistent development DATABASE_URL')
    if os.environ.get('DATABASE_URL') and os.environ['DATABASE_URL'] != url:
        raise TargetError('Process DATABASE_URL differs from root .env; target is ambiguous')
    if any(os.environ.get(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS')):
        raise TargetError('Libpq target or session overrides are set in the process environment')
    try:
        parsed = urlsplit(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        mode = params.get('sslmode', [])
        hostname = parsed.hostname.lower() if parsed.hostname else ''
        endpoint = hostname.split('.')[0] if hostname.endswith('.neon.tech') else ''
        if (parsed.scheme not in ('postgres', 'postgresql')
                or endpoint != dev_endpoint or endpoint in (prod_endpoint, disposable_endpoint)
                or parsed.path != '/neondb' or parsed.port not in (None, 5432)
                or mode not in (['require'], ['verify-ca'], ['verify-full'])
                or any(key in params for key in ('host', 'hostaddr', 'service', 'options', 'dbname', 'port'))):
            raise TargetError('Root .env URL is not the selected direct development endpoint with explicit TLS')
    except ValueError:
        raise TargetError('Root .env URL is invalid or has unsupported target options') from None
    return {'url': url, 'branch_id': dev_branch, 'endpoint_id': dev_endpoint,
            'sslmode': mode[0]}


def _connection(config: dict, *, read_only: bool):
    import psycopg
    return psycopg.connect(
        config['url'], sslmode=config['sslmode'],
        options='-c default_transaction_read_only=on' if read_only else '',
        autocommit=True, connect_timeout=10, prepare_threshold=None,
        application_name='observatorio-ndq-development-initialization')


def _inspect_empty_target(conn) -> dict:
    if client_tls_details(conn)['tls_active'] is not True:
        raise TargetError('Client libpq did not confirm active TLS')
    database = conn.execute('SELECT current_database()').fetchone()[0]
    if database != 'neondb':
        raise TargetError('Connected database is not neondb')
    schema, ledger = conn.execute(
        "SELECT to_regnamespace('ndq'), to_regclass('public.ndq_schema_migrations')").fetchone()
    if schema is not None or ledger is not None:
        raise TargetError('Development database already has NDQ schema or migration ledger')
    return {'database': 'neondb', 'tls_active': True,
            'tls_evidence': 'libpq_client_ssl_in_use',
            'certificate_validation': 'not_independently_observed',
            'ndq_schema_exists': False, 'migration_table_exists': False,
            'url_matches_supplied_development_endpoint_id': True,
            'branch_id_verified_by_neon_api': False}


def run(*, apply: bool, confirm_branch_id: str | None = None) -> dict:
    config = read_config()
    if apply and confirm_branch_id != config['branch_id']:
        raise TargetError('Confirmed branch ID does not match the selected development branch')
    pilots = load_pilots()
    if len(pilots) != 3:
        raise TargetError('Expected exactly three pilot dossiers')
    with _connection(config, read_only=True) as conn:
        with conn.transaction():
            if conn.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
                raise TargetError('Read-only preflight could not be established')
            preflight = _inspect_empty_target(conn)
    if not apply:
        return {'mode': 'preflight_only', 'preflight_target': preflight,
                'pilots_validated_offline': 3, 'writes_performed': False}
    # A fresh write-capable session must pass the same target checks before migrate().
    with _connection(config, read_only=False) as conn:
        _inspect_empty_target(conn)
        applied = migrate(conn, through=1)
        if applied != ['001_foundation.sql'] or migrate(conn, through=1):
            raise AssertionError('Migration 001 did not apply exactly once')
        first = import_pilots(conn, pilots)
        if len(first['imported']) != 3 or first['unchanged']:
            raise AssertionError('First import did not create exactly three dossiers')
        before = _counts(conn)
        second = import_pilots(conn, pilots)
        if second['imported'] or len(second['unchanged']) != 3 or second['run_id'] is not None:
            raise AssertionError('Repeated import was not a no-op')
        checks = _verify(conn, pilots, before)
    return {'mode': 'persistent_development_initialization',
            'preflight_target': preflight, 'applied_migrations': applied,
            'first_imported': 3, 'second_import_unchanged': 3,
            'checks': checks, 'writes_performed': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Guarded persistent Neon development initialization')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preflight', action='store_true', help='Read-only target and pilot checks')
    mode.add_argument('--apply', action='store_true', help='Write only after explicit authorization')
    parser.add_argument('--confirm-branch-id', help='Development branch ID checked in the Neon console')
    args = parser.parse_args(argv)
    try:
        result = run(apply=args.apply, confirm_branch_id=args.confirm_branch_id)
    except Exception as exc:
        # Never emit driver messages, URLs, hosts, or configuration values.
        result = {'success': False, 'error_type': type(exc).__name__,
                  'diagnosis': 'Initialization stopped; inspect local guard configuration and Neon branch selection',
                  'partial_changes_possible': args.apply}
        sqlstate = getattr(exc, 'sqlstate', None)
        if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate):
            result['sqlstate'] = sqlstate
        status = 1
    else:
        result['success'] = True
        status = 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status


if __name__ == '__main__':
    sys.exit(main())
