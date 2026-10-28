"""Guarded, synthetic migration-002 validation on one disposable Neon branch.

Default mode is read-only. This module never reads root DATABASE_URL for a target,
never fetches an official API, and never writes editorial or public content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

from tools.collectors.http import BASE, FetchResult
from tools.collectors.camara.normalize import normalize

from .check_connection import client_tls_details
from .db import MIGRATIONS
from .preflight_source_disposable_neon import inspect as inspect_foundation
from .source_observations import _write_validated_observation, validate_source_observation
from .validate_disposable_neon import _connection, read_config

MIGRATION = '002_source_observations.sql'
SYNTHETIC_ID = 990000042
CONCURRENT_ID = 990000043
SOURCE_TABLES = ('source_snapshots', 'source_snapshot_items', 'source_collection_attempts',
                 'source_attempt_items', 'source_change_candidates',
                 'source_editorial_proposals', 'source_editorial_decisions')


class DisposableSourceError(ValueError):
    pass


def _connected_target(conn, config: dict) -> None:
    """Confirm libpq's effective destination, not only the URL's text."""
    expected = urlsplit(config['url']).hostname
    try:
        params = conn.info.get_parameters()
        # Psycopg resolves the named host before connecting and supplies that IP
        # as hostaddr. Reject explicit overrides at read_config()/_config_guard();
        # the effective hostaddr alone does not establish its provenance.
        if (params.get('host', '').lower() != expected.lower()
                or params.get('dbname') != 'neondb'
                or params.get('service')):
            raise DisposableSourceError('Connected target differs from selected disposable endpoint')
        if client_tls_details(conn)['tls_active'] is not True:
            raise DisposableSourceError('Client TLS not confirmed on disposable connection')
    except DisposableSourceError:
        raise
    except Exception:
        raise DisposableSourceError('Cannot confirm effective disposable connection target') from None


def _config_guard(config: dict) -> None:
    if any(os.environ.get(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS')):
        raise DisposableSourceError('Libpq target or session override is present')
    if not re.fullmatch(r'br-[a-z0-9-]+', config['branch_id']):
        raise DisposableSourceError('Invalid disposable branch confirmation target')
    if config.get('development_endpoint_verified') is not True:
        raise DisposableSourceError('Development endpoint separation guard is unavailable')
    # read_config() has already checked the direct endpoint, TLS URL, database,
    # protected IDs and development URL separation. Never return or print its URL.


def _pilot_state(conn) -> tuple:
    """Capture every pilot identity/revision/review/publication row for equality."""
    from .contract import load_pilots
    pilots = load_pilots()
    ids = sorted(p.record['dossier_id'] for p in pilots)
    if len(ids) != 3 or len(set(ids)) != 3:
        raise DisposableSourceError('Expected exactly three distinct pilot dossiers')
    dossier_rows = conn.execute('''SELECT dossier_id, kind FROM ndq.dossiers ORDER BY dossier_id''').fetchall()
    if sorted(row[0] for row in dossier_rows) != ids:
        raise DisposableSourceError('Disposable branch has unexpected dossier identities')
    revisions = conn.execute('''SELECT r.dossier_id, r.revision_id::text, r.revision_number,
        r.previous_revision_id::text, r.content_hash, e.review_state, p.state
        FROM ndq.dossier_revisions r
        JOIN ndq.editorial_reviews e ON e.revision_id = r.revision_id
        JOIN ndq.publication_states p ON p.revision_id = r.revision_id
        ORDER BY r.dossier_id, r.revision_number''').fetchall()
    if len(revisions) != 3 or sorted(row[0] for row in revisions) != ids:
        raise DisposableSourceError('Expected one reviewed revision per pilot')
    expected_hash = {pilot.record['dossier_id']: pilot.content_hash for pilot in pilots}
    if any(row[2] != 1 or row[3] is not None or row[4] != expected_hash[row[0]]
           or row[5] != 'pending_review' or row[6] != 'private_preview_only'
           for row in revisions):
        raise DisposableSourceError('Pilot revision, review or publication baseline differs')
    identities = conn.execute('''SELECT institution, namespace, value, dossier_id
        FROM ndq.official_identities ORDER BY institution, namespace, value''').fetchall()
    expected_identities = {(item['institution'], item['namespace'], str(item['value']),
                            pilot.record['dossier_id'])
                           for pilot in pilots for item in pilot.record['official_identities']}
    if set(identities) != expected_identities:
        raise DisposableSourceError('Pilot official identities are missing or unexpected')
    return (tuple(dossier_rows), tuple(revisions), tuple(identities))


def _migration_checksum() -> tuple[str, str]:
    source = (MIGRATIONS / MIGRATION).read_text(encoding='utf-8')
    return source, hashlib.sha256(source.encode('utf-8')).hexdigest()


def _apply_only_002(conn) -> None:
    source, checksum = _migration_checksum()
    foundation = (MIGRATIONS / '001_foundation.sql').read_text(encoding='utf-8')
    expected_001 = hashlib.sha256(foundation.encode('utf-8')).hexdigest()
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(784331, 1)')
        applied = dict(conn.execute('SELECT name, sha256 FROM public.ndq_schema_migrations').fetchall())
        if applied != {'001_foundation.sql': expected_001}:
            raise DisposableSourceError('Migration 001 state changed before write')
        if conn.execute("SELECT to_regclass('ndq.source_snapshots')").fetchone()[0] is not None:
            raise DisposableSourceError('Source tables appeared before migration')
        conn.execute(source, prepare=False)
        conn.execute('INSERT INTO public.ndq_schema_migrations (name, sha256) VALUES (%s, %s)',
                     (MIGRATION, checksum))


def _fixture(pid: int, *, variant='A', events_complete=True, ambiguous=False) -> dict:
    """Synthetic, selected API-shaped data. No HTTP request or official claim."""
    if pid not in (SYNTHETIC_ID, CONCURRENT_ID):
        raise DisposableSourceError('Unsupported synthetic identity')
    endpoint = f'{BASE}/proposicoes/{pid}'
    event = {'dataHora': '2025-01-02T00:00', 'sequencia': 1, 'siglaOrgao': 'TESTE',
             'descricaoTramitacao': 'Tramitação sintética', 'despacho': 'Texto sintético A'}
    if variant in ('B', 'D', 'E', 'F'):
        event['despacho'] = 'Texto sintético B'
    if variant == 'G':
        event['despacho'] = 'Texto sintético G para rollback'
    if variant in ('D', 'E', 'F'):
        event['dataHora'] = '2025-01-03T00:00'
        event['siglaOrgao'] = 'TESTE2'
    events = [event]
    if variant in ('C', 'D', 'E', 'F'):
        events.append(dict(event, sequencia=2, siglaOrgao='TESTE3'))
    if ambiguous:
        events.append(dict(event, despacho='Linha sintética ambígua'))
    data = {
        'proposition': {'id': pid, 'uri': endpoint, 'siglaTipo': 'TESTE',
                        'numero': 1, 'ano': 2025, 'ementa': 'Proposição sintética NDQ',
                        'dataApresentacao': '2025-01-02'},
        'events': events,
        'authors': [{'uri': f'{BASE}/deputados/{pid}', 'nome': 'AUTOR SINTÉTICO NDQ'}],
        'relationships': [],
    }
    suffixes = {'proposition': '', 'events': '/tramitacoes',
                'authors': '/autores', 'relationships': '/relacionadas'}
    stamp = '2026-01-10T12:00:00Z'
    fetched = {name: FetchResult(endpoint + suffix, data[name],
               events_complete if name == 'events' else True,
               [{'url': endpoint + suffix, 'retrieved_at': stamp}])
               for name, suffix in suffixes.items()}
    return normalize(pid, fetched, stamp)


def _write(conn, observation: dict) -> dict:
    return _write_validated_observation(conn, validate_source_observation(observation))


def _source_counts(conn) -> dict[str, int]:
    return {table: conn.execute(f'SELECT count(*) FROM ndq.{table}').fetchone()[0]
            for table in SOURCE_TABLES}


def _assert_sql_rejected(conn, query: str, params=()) -> None:
    from psycopg import Error
    with conn.transaction(force_rollback=True):
        try:
            with conn.transaction():
                conn.execute(query, params)
        except Error:
            return
        raise DisposableSourceError('Expected SQL constraint or append-only rejection')


def _verify_constraints(conn) -> dict:
    with conn.transaction():
        foreign_keys = conn.execute('''SELECT count(*), count(*) FILTER (WHERE NOT convalidated)
            FROM pg_constraint WHERE contype = 'f' AND connamespace = 'ndq'::regnamespace''').fetchone()
        triggers = conn.execute('''SELECT count(*) FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'ndq' AND c.relname = ANY(%s) AND t.tgname = 'preserve_history'
              AND NOT t.tgisinternal''', (list(SOURCE_TABLES),)).fetchone()[0]
        if foreign_keys[0] < 36 or foreign_keys[1] != 0 or triggers != len(SOURCE_TABLES):
            raise DisposableSourceError('Foreign keys or append-only triggers are incomplete')
        if conn.execute('SELECT count(*) FROM ndq.source_snapshots WHERE NOT synthetic_fixture').fetchone()[0]:
            raise DisposableSourceError('Non-synthetic source snapshot found')
        if conn.execute('SELECT count(*) FROM ndq.source_collection_attempts WHERE NOT synthetic_fixture').fetchone()[0]:
            raise DisposableSourceError('Non-synthetic source attempt found')
    _assert_sql_rejected(conn, 'UPDATE ndq.source_snapshots SET content_hash = content_hash')
    _assert_sql_rejected(conn, 'DELETE FROM ndq.source_collection_attempts')
    _assert_sql_rejected(conn, 'TRUNCATE ndq.source_snapshot_items')
    _assert_sql_rejected(conn, '''INSERT INTO ndq.source_snapshot_items
        (snapshot_id,endpoint_category,ordinal,locator,selected_fields,fingerprint,
         evidence_url,source_date_semantic) VALUES
        (%s,'authors',0,'{}'::jsonb,'{}'::jsonb,%s,%s,'not_supplied')''',
        (uuid4(), '0'*64, f'{BASE}/proposicoes/{SYNTHETIC_ID}/autores'))
    return {'foreign_keys_total': foreign_keys[0], 'foreign_keys_unvalidated': 0,
            'append_only_triggers': triggers, 'mutation_and_fk_rejections': 4}


def _run_scenarios(conn) -> dict:
    if any(_source_counts(conn).values()):
        raise DisposableSourceError('Source validation tables are not empty after migration')
    a = _fixture(SYNTHETIC_ID)
    first, same = _write(conn, a), _write(conn, a)
    b = _write(conn, _fixture(SYNTHETIC_ID, variant='B'))
    returned = _write(conn, a)
    if ([x['transition'] for x in (first, same, b, returned)] !=
            ['initial', 'unchanged', 'changed', 'returned']):
        raise DisposableSourceError('A-A-B-A transition failed')
    alerts = conn.execute('SELECT count(*) FROM ndq.source_change_candidates').fetchone()[0]
    again_b, again_a = _write(conn, _fixture(SYNTHETIC_ID, variant='B')), _write(conn, a)
    if (again_b['transition'] != 'returned' or again_a['transition'] != 'returned'
            or conn.execute('SELECT count(*) FROM ndq.source_change_candidates').fetchone()[0] != alerts):
        raise DisposableSourceError('Duplicate change candidate was created')
    partial = _write(conn, _fixture(SYNTHETIC_ID, events_complete=False))
    recovered = _write(conn, a)
    if partial['outcome'] != 'incomplete' or recovered['transition'] != 'unchanged':
        raise DisposableSourceError('Partial retrieval changed the factual baseline')
    new_event = _write(conn, _fixture(SYNTHETIC_ID, variant='C'))
    correction = _write(conn, _fixture(SYNTHETIC_ID, variant='D'))
    ambiguous = _write(conn, _fixture(SYNTHETIC_ID, variant='F', ambiguous=True))
    if (new_event['new_change_candidates'] < 1 or correction['new_change_candidates'] < 1
            or ambiguous['new_change_candidates'] < 1):
        raise DisposableSourceError('Event-change scenario produced no candidate')
    classes = {row[0] for row in conn.execute('''SELECT classification FROM ndq.source_change_candidates
        WHERE official_id = %s''', (str(SYNTHETIC_ID),)).fetchall()}
    if not {'new', 'changed', 'uncertain', 'conflicting'} <= classes:
        raise DisposableSourceError('Expected event correction and ambiguity classes missing')
    before = _source_counts(conn)
    class FailingProxy:
        def __init__(self, real): self.real = real
        def transaction(self): return self.real.transaction()
        def execute(self, query, params=()):
            if 'INSERT INTO ndq.source_snapshot_items' in query:
                raise RuntimeError('injected_failure')
            return self.real.execute(query, params)
    rolled_back = False
    try:
        _write(FailingProxy(conn), _fixture(SYNTHETIC_ID, variant='G'))
    except RuntimeError:
        rolled_back = _source_counts(conn) == before
    if not rolled_back:
        raise DisposableSourceError('Injected failure did not roll back its transaction')
    counts = _source_counts(conn)
    if (counts['source_snapshots'] != 5 or counts['source_collection_attempts'] != 11
            or counts['source_editorial_proposals'] != 0
            or counts['source_editorial_decisions'] != 0):
        raise DisposableSourceError('Synthetic transaction or editorial counts differ')
    return {'a_a_b_a': True, 'duplicate_candidate_prevented': True,
            'partial_recovery': True, 'new_event': True, 'event_correction_ambiguous': True,
            'injected_failure_rolled_back': True, 'successful_ingestion_transactions': 11}


def _run_concurrency(config: dict) -> dict:
    source = _fixture(CONCURRENT_ID)
    barrier = Barrier(2)
    def worker():
        with _connection(config['url'], config['sslmode'], read_only=False) as conn:
            _connected_target(conn, config)
            if conn.execute('SELECT current_database()').fetchone()[0] != 'neondb':
                raise DisposableSourceError('Concurrent connection database mismatch')
            barrier.wait(timeout=20)
            return _write(conn, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        results = [future.result(timeout=45) for future in futures]
    if (sorted(x['snapshot_created'] for x in results) != [False, True]
            or sorted(x['transition'] for x in results) != ['initial', 'unchanged']):
        raise DisposableSourceError('Concurrent deduplication failed')
    with _connection(config['url'], config['sslmode'], read_only=True) as conn:
        _connected_target(conn, config)
        counts = conn.execute('''SELECT
            (SELECT count(*) FROM ndq.source_snapshots WHERE official_id = %s),
            (SELECT count(*) FROM ndq.source_collection_attempts WHERE official_id = %s),
            (SELECT count(*) FROM ndq.source_change_candidates WHERE official_id = %s)''',
            (str(CONCURRENT_ID),)*3).fetchone()
    if counts != (1, 2, 0):
        raise DisposableSourceError('Concurrent source counts differ from expected')
    return {'connections': 2, 'snapshots': 1, 'attempts': 2, 'change_candidates': 0}


def run(*, apply=False, confirm_branch_id=None, config_reader=read_config,
        connection=_connection) -> dict:
    config = config_reader()
    _config_guard(config)
    if apply and (not isinstance(confirm_branch_id, str)
                  or not re.fullmatch(r'br-[a-z0-9-]+', confirm_branch_id)
                  or confirm_branch_id != config['branch_id']):
        raise DisposableSourceError('Manual disposable branch ID confirmation does not match')
    with connection(config['url'], config['sslmode'], read_only=True) as conn:
        _connected_target(conn, config)
        preflight = inspect_foundation(conn)
        before = _pilot_state(conn)
    if not apply:
        return {'mode': 'read_only_preflight', 'preflight': preflight,
                'pilot_dossiers_checked': len(before[0]), 'writes_performed': False}
    with connection(config['url'], config['sslmode'], read_only=False) as conn:
        _connected_target(conn, config)
        inspect_foundation(conn)  # Recheck the write-capable session read-only first.
        if _pilot_state(conn) != before:
            raise DisposableSourceError('Pilot state changed between preflight and write connection')
        _apply_only_002(conn)
        scenarios = _run_scenarios(conn)
        constraints = _verify_constraints(conn)
        if _pilot_state(conn) != before:
            raise DisposableSourceError('Pilot state changed during source validation')
        counts = _source_counts(conn)
    concurrency = _run_concurrency(config)
    with connection(config['url'], config['sslmode'], read_only=True) as conn:
        _connected_target(conn, config)
        if _pilot_state(conn) != before:
            raise DisposableSourceError('Pilot state changed after concurrency test')
        ledger = dict(conn.execute('SELECT name, sha256 FROM public.ndq_schema_migrations').fetchall())
        if ledger.get(MIGRATION) != _migration_checksum()[1] or set(ledger) != {'001_foundation.sql', MIGRATION}:
            raise DisposableSourceError('Final migration ledger mismatch')
        final_counts = _source_counts(conn)
        if (final_counts['source_snapshots'] != 6 or final_counts['source_collection_attempts'] != 13
                or final_counts['source_editorial_proposals'] != 0
                or final_counts['source_editorial_decisions'] != 0):
            raise DisposableSourceError('Final synthetic source counts differ')
        if conn.execute('''SELECT count(*) FROM ndq.source_snapshots
            WHERE official_id NOT IN (%s,%s)''',
            (str(SYNTHETIC_ID), str(CONCURRENT_ID))).fetchone()[0]:
            raise DisposableSourceError('Source snapshot escaped synthetic identities')
    return {'mode': 'guarded_disposable_source_validation', 'preflight': preflight,
            'migration_002_applied': True, 'scenarios': scenarios,
            'constraints': constraints, 'concurrency': concurrency,
            'pilot_dossiers_unchanged': True, 'pilot_revisions_review_publication_unchanged': True,
            'source_table_counts_before_concurrency': counts, 'source_table_counts_final': final_counts,
            'synthetic_state_left_in_disposable_branch': True,
            'writes_performed': True, 'branch_id_verified_by_neon_api': False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Guarded disposable Neon source validation; default read-only')
    parser.add_argument('--apply', action='store_true', help='Explicitly run migration 002 and synthetic tests')
    parser.add_argument('--confirm-branch-id', help='Disposable br-... ID checked manually in the Neon console')
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
        result = {'success': False, 'mode': 'guarded_disposable_source_validation' if args.apply else 'read_only_preflight',
                  'error_type': type(exc).__name__, 'partial_changes_possible': args.apply,
                  'diagnosis': 'Validation stopped; inspect the selected disposable branch and local configuration'}
        sqlstate = getattr(exc, 'sqlstate', None)
        if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate):
            result['sqlstate'] = sqlstate
        status = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
