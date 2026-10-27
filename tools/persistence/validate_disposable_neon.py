"""Validate migration 001 and pilot imports on one explicitly selected Neon branch.

This command never reads DATABASE_URL for its target and never uses the localhost
integration-test variable. The separate config is intentionally ignored by Git.
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
from .contract import SECTIONS, load_pilots
from .db import ROOT, migrate
from .repository import import_pilots, read_record

CONFIG = ROOT / '.env.ndq-validation'
TABLES = ('collection_runs', 'run_sources', 'source_systems', 'dossiers',
          'dossier_slugs', 'dossier_revisions', 'document_references',
          'observations', 'observation_evidence', 'official_identities',
          'identity_observations', 'facts', 'events', 'contextual_milestones',
          'status_observations', 'actors', 'actor_roles', 'official_acts',
          'relationships', 'documented_changes', 'editorial_reviews',
          'editorial_basis', 'publication_states', 'scheduled_checks',
          'check_results')


class TargetError(ValueError):
    pass


def _host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _endpoint(host: str | None) -> str | None:
    if not host or not host.lower().endswith('.neon.tech'):
        return None
    first = host.lower().split('.')[0]
    return first.removesuffix('-pooler')


def read_config(path: Path = CONFIG) -> dict:
    """Read only a named, local config; never fall back to the dev URL."""
    from dotenv import dotenv_values

    if not path.is_file():
        raise TargetError('Create the ignored .env.ndq-validation file first')
    values = dotenv_values(path)
    required = ('NDQ_DISPOSABLE_DATABASE_URL', 'NDQ_DISPOSABLE_BRANCH_ID',
                'NDQ_DISPOSABLE_ENDPOINT_ID', 'NDQ_DEVELOPMENT_BRANCH_ID',
                'NDQ_PRODUCTION_BRANCH_ID', 'NDQ_PRODUCTION_ENDPOINT_ID')
    if any(not values.get(key) for key in required):
        raise TargetError('Disposable URL and disposable, development, production branch/endpoint identifiers are required')
    url = values['NDQ_DISPOSABLE_DATABASE_URL']
    branch_id = values['NDQ_DISPOSABLE_BRANCH_ID']
    endpoint_id = values['NDQ_DISPOSABLE_ENDPOINT_ID']
    production_id = values['NDQ_PRODUCTION_ENDPOINT_ID']
    development_branch_id = values['NDQ_DEVELOPMENT_BRANCH_ID']
    production_branch_id = values['NDQ_PRODUCTION_BRANCH_ID']
    if any(not re.fullmatch(r'br-[a-z0-9-]+', value or '')
           for value in (branch_id, development_branch_id, production_branch_id)):
        raise TargetError('Invalid branch ID format')
    if branch_id in (development_branch_id, production_branch_id):
        raise TargetError('Disposable branch ID matches a protected branch ID')
    if any(not re.fullmatch(r'ep-[a-z0-9-]+', value or '') for value in (endpoint_id, production_id)):
        raise TargetError('Invalid endpoint ID format')
    if endpoint_id == production_id:
        raise TargetError('Disposable endpoint matches the production endpoint')
    try:
        parsed = urlsplit(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        mode = params.get('sslmode', [])
        if (parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname
                or _endpoint(parsed.hostname) != endpoint_id
                or parsed.hostname.lower().split('.')[0].endswith('-pooler')
                or parsed.path != '/neondb' or parsed.port not in (None, 5432)
                or mode not in (['require'], ['verify-ca'], ['verify-full'])
                or any(key in params for key in ('host', 'hostaddr', 'service', 'options'))):
            raise TargetError('Disposable URL must name the selected direct Neon endpoint, neondb and explicit TLS')
    except ValueError:
        raise TargetError('Disposable URL is invalid or has unsupported connection options') from None
    # Check both possible persistent-development values without displaying either.
    dev_urls = [os.environ.get('DATABASE_URL', '')]
    dev_file = ROOT / '.env'
    if dev_file.is_file():
        dev_urls.append(dotenv_values(dev_file).get('DATABASE_URL', ''))
    if any(_endpoint(_host(value)) == endpoint_id for value in dev_urls if value):
        raise TargetError('Disposable endpoint matches the persistent development endpoint')
    return {'url': url, 'branch_id': branch_id, 'endpoint_id': endpoint_id,
            'sslmode': mode[0]}


def _connection(url: str, sslmode: str, *, read_only: bool):
    import psycopg
    return psycopg.connect(
        url, sslmode=sslmode,
        options='-c default_transaction_read_only=on' if read_only else '',
        autocommit=True, connect_timeout=10, prepare_threshold=None,
        application_name='observatorio-ndq-disposable-validation')


def _inspect_target(conn, config: dict, *, must_be_empty: bool) -> dict:
    if client_tls_details(conn)['tls_active'] is not True:
        raise TargetError('Client libpq did not confirm active TLS')
    if conn.execute('SELECT current_database()').fetchone()[0] != 'neondb':
        raise TargetError('Connected database is not neondb')
    schema, ledger = conn.execute("SELECT to_regnamespace('ndq'), to_regclass('public.ndq_schema_migrations')").fetchone()
    if must_be_empty and (schema is not None or ledger is not None):
        raise TargetError('Selected database already contains NDQ schema or migration ledger')
    return {'tls_active': True, 'tls_evidence': 'libpq_client_ssl_in_use',
            'certificate_validation': 'not_independently_observed',
            'database': 'neondb', 'ndq_schema_exists': schema is not None,
            'migration_table_exists': ledger is not None,
            'url_endpoint_matches_selected_id': True,
            'distinct_from_supplied_protected_branch_ids_and_endpoints': True,
            'branch_id_verified_by_neon_api': False}


def _counts(conn) -> dict[str, int]:
    # Identifiers are fixed by this module, never read from the config.
    return {table: conn.execute(f'SELECT count(*) FROM ndq.{table}').fetchone()[0]
            for table in TABLES}


def _verify(conn, pilots, before: dict) -> dict:
    expected = {p.record['dossier_id']: p for p in pilots}
    rows = conn.execute('''SELECT dossier_id, revision_id, revision_number,
        previous_revision_id, content_hash FROM ndq.dossier_revisions''').fetchall()
    if (len(rows) != len(expected) or {r[0] for r in rows} != set(expected)
            or any(r[2] != 1 or r[3] is not None or r[4] != expected[r[0]].content_hash for r in rows)):
        raise AssertionError('First revision chain or content hash mismatch')
    for dossier_id, revision_id, *_ in rows:
        if read_record(conn, revision_id) != expected[dossier_id].record:
            raise AssertionError('Stored pilot did not round-trip exactly')
    counts = _counts(conn)
    expected_counts = {'dossiers': len(pilots), 'dossier_revisions': len(pilots),
                       'collection_runs': 1,
                       'document_references': sum(len(p.record['source_references']) for p in pilots),
                       'official_identities': sum(len(p.record['official_identities']) for p in pilots),
                       'identity_observations': sum(len(p.record['official_identities']) for p in pilots),
                       'observations': sum(len(p.record.get(section, [])) for p in pilots for section in SECTIONS),
                       'observation_evidence': sum(len(item.get('basis_source_ids' if section == 'change_history' else 'source_ids', []))
                                                   for p in pilots for section in SECTIONS for item in p.record.get(section, []))}
    expected_counts.update({table: sum(len(p.record.get(section, [])) for p in pilots)
                            for table, section in (('facts', 'facts'), ('events', 'events'),
                                                   ('contextual_milestones', 'contextual_milestones'),
                                                   ('status_observations', 'official_status_observations'),
                                                   ('actor_roles', 'actor_roles'),
                                                   ('documented_changes', 'change_history'))})
    if any(counts[table] != value for table, value in expected_counts.items()):
        raise AssertionError('Imported table counts differ from pilot input')
    if counts['editorial_reviews'] != len(pilots) or counts['publication_states'] != len(pilots):
        raise AssertionError('Review or publication state count mismatch')
    if conn.execute("SELECT count(*) FROM ndq.editorial_reviews WHERE review_state <> 'pending_review'").fetchone()[0]:
        raise AssertionError('Unexpected editorial review state')
    if conn.execute("SELECT count(*) FROM ndq.publication_states WHERE state <> 'private_preview_only'").fetchone()[0]:
        raise AssertionError('Unexpected publication state')
    if conn.execute('''SELECT count(*) FROM ndq.official_identities i
        JOIN ndq.dossier_revisions r ON r.revision_id = i.first_revision_id
        WHERE i.dossier_id <> r.dossier_id''').fetchone()[0]:
        raise AssertionError('Official identity owner mismatch')
    if conn.execute('''SELECT count(*) FROM ndq.document_references d
        JOIN ndq.observations o ON o.revision_id = d.revision_id
            AND o.primary_source_id = d.source_id''').fetchone()[0] != counts['observations']:
        raise AssertionError('Observation primary source reference mismatch')
    if conn.execute('''SELECT count(*) FROM ndq.observation_evidence e
        JOIN ndq.document_references d ON d.revision_id = e.revision_id
            AND d.source_id = e.source_id''').fetchone()[0] != counts['observation_evidence']:
        raise AssertionError('Observation evidence reference mismatch')
    foreign_keys = conn.execute('''SELECT count(*), count(*) FILTER (WHERE NOT convalidated)
        FROM pg_constraint WHERE contype = 'f' AND connamespace = 'ndq'::regnamespace''').fetchone()
    if not foreign_keys[0] or foreign_keys[1]:
        raise AssertionError('Missing or unvalidated NDQ foreign key constraints')
    if before != counts:
        raise AssertionError('Repeated import changed table counts')
    return {'counts': counts, 'foreign_keys_validated': foreign_keys[0],
            'exact_roundtrips': len(rows), 'revisions_per_dossier': 1,
            'identity_ownership_checked': True, 'source_references_checked': True,
            'review_and_publication_states_checked': True}


def run(*, apply: bool, confirm_branch_id: str | None = None) -> dict:
    config = read_config()
    if apply and confirm_branch_id != config['branch_id']:
        raise TargetError('Confirmation branch ID does not match the selected disposable branch')
    pilots = load_pilots()
    if len(pilots) != 3:
        raise TargetError('Expected exactly the three pilot dossiers')
    with _connection(config['url'], config['sslmode'], read_only=True) as conn:
        with conn.transaction():
            if conn.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
                raise TargetError('Read-only preflight could not be established')
            target = _inspect_target(conn, config, must_be_empty=True)
    if not apply:
        return {'mode': 'preflight_only', 'preflight_target': target, 'pilots_validated_offline': len(pilots),
                'writes_performed': False}
    with _connection(config['url'], config['sslmode'], read_only=False) as conn:
        _inspect_target(conn, config, must_be_empty=True)
        applied = migrate(conn)
        if applied != ['001_foundation.sql'] or migrate(conn):
            raise AssertionError('Migration 001 did not apply exactly once')
        first = import_pilots(conn, pilots)
        if len(first['imported']) != 3 or first['unchanged']:
            raise AssertionError('First import did not create exactly three dossiers')
        before = _counts(conn)
        second = import_pilots(conn, pilots)
        if second['imported'] or len(second['unchanged']) != 3 or second['run_id'] is not None:
            raise AssertionError('Repeated import was not a no-op')
        checks = _verify(conn, pilots, before)
    return {'mode': 'disposable_branch_validation', 'preflight_target': target,
            'applied_migrations': applied, 'first_imported': 3,
            'second_import_unchanged': 3, 'checks': checks,
            'writes_performed': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Validate on an explicitly selected disposable Neon branch')
    parser.add_argument('--apply', action='store_true', help='Apply migration and import pilots after preflight')
    parser.add_argument('--confirm-branch-id', help='Repeat the disposable branch ID from the Neon console')
    args = parser.parse_args(argv)
    try:
        result = run(apply=args.apply, confirm_branch_id=args.confirm_branch_id)
    except Exception as exc:
        # libpq and URL exception messages can contain credentials or hostnames.
        result = {'success': False, 'error_type': type(exc).__name__,
                  'diagnosis': 'Validation stopped; inspect the selected branch and local configuration',
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
