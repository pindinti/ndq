"""Offline-only tests for the guarded development migration-002 executor."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from tools.persistence import migrate_source_development_neon as executor
from tools.persistence import preflight_source_disposable_neon as preflight
from tools.persistence import validate_source_disposable_neon as disposable
from tools.persistence.contract import load_pilots
from tools.persistence.db import MIGRATIONS

URL = 'postgresql://fixture@ep-development.neon.tech/neondb?sslmode=require'
CONFIG = {'url': URL, 'branch_id': 'br-development',
          'endpoint_id': 'ep-development', 'sslmode': 'require'}
FOUNDATION = hashlib.sha256((MIGRATIONS / '001_foundation.sql').read_text(
    encoding='utf-8').encode('utf-8')).hexdigest()
SOURCE_SQL = (MIGRATIONS / executor.MIGRATION).read_text(encoding='utf-8')
SOURCE_HASH = hashlib.sha256(SOURCE_SQL.encode('utf-8')).hexdigest()
EXPECTED_SOURCE_CONSTRAINTS = executor.SOURCE_CONSTRAINTS.copy()


class State:
    def __init__(self):
        self.ledger = {'001_foundation.sql': FOUNDATION}
        self.migrated = False
        self.source_counts = {table: 0 for table in executor.SOURCE_TABLES}
        self.calls = []


class Connection:
    def __init__(self, state: State, *, host='ep-development.neon.tech', tls=True):
        self.state = state
        self.host = host
        self.tls = tls
        self.info = self

    def get_parameters(self):
        return {'host': self.host, 'dbname': 'neondb', 'hostaddr': '192.0.2.41'}

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def transaction(self): return nullcontext()

    def execute(self, query, params=(), **kwargs):
        self.state.calls.append((query, params, kwargs))
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchone(self): return self.rows[0]
            def fetchall(self): return self.rows
        if query == SOURCE_SQL:
            self.state.migrated = True
            return Result([])
        if query.startswith('INSERT INTO public.ndq_schema_migrations'):
            self.state.ledger[params[0]] = params[1]
            return Result([])
        if query.startswith('SELECT name, sha256'):
            return Result(list(self.state.ledger.items()))
        if query.startswith('SELECT current_database()'):
            return Result([('neondb',)])
        if query.startswith('SHOW transaction_read_only'):
            return Result([('on',)])
        if query.startswith('SELECT to_regnamespace'):
            return Result([('ndq', 'public.ndq_schema_migrations')])
        if query.startswith("SELECT to_regclass('ndq.source_snapshots')"):
            return Result([('ndq.source_snapshots' if self.state.migrated else None,)])
        if query.startswith('SELECT to_regclass(%s)'):
            return Result([(params[0] if self.state.migrated else None,)])
        if query.startswith('SELECT count(*) FROM ndq.source_'):
            return Result([(self.state.source_counts[query.split('ndq.')[1]],)])
        if "count(*) FILTER (WHERE contype = 'f')" in query:
            return Result([(47, 0)])
        if 'GROUP BY t.relname' in query:
            return Result([(name, *counts) for name, counts in EXPECTED_SOURCE_CONSTRAINTS.items()])
        if "WHERE c.contype = 'f'" in query:
            return Result([(11,)])
        if 'count(DISTINCT c.relname)' in query:
            return Result([(7,)])
        if query.startswith('SELECT pg_advisory_xact_lock') or query.startswith('SET TRANSACTION'):
            return Result([])
        raise AssertionError('Unexpected SQL in offline fake')


class DevelopmentSourceMigrationOfflineTest(unittest.TestCase):
    def config_file(self, directory: str, *, url=URL, development='br-development',
                    development_endpoint='ep-development', disposable='br-disposable',
                    production='br-production') -> Path:
        path = Path(directory) / '.env.ndq-development'
        path.write_text('\n'.join((
            f'NDQ_DEVELOPMENT_DATABASE_URL={url}',
            f'NDQ_DEVELOPMENT_BRANCH_ID={development}',
            f'NDQ_DEVELOPMENT_ENDPOINT_ID={development_endpoint}',
            f'NDQ_DISPOSABLE_BRANCH_ID={disposable}',
            'NDQ_DISPOSABLE_ENDPOINT_ID=ep-disposable',
            f'NDQ_PRODUCTION_BRANCH_ID={production}',
            'NDQ_PRODUCTION_ENDPOINT_ID=ep-production',
        )), encoding='utf-8')
        return path

    def tls(self, active=True):
        return (patch.object(disposable, 'client_tls_details', return_value={'tls_active': active}),
                patch.object(preflight, 'client_tls_details', return_value={'tls_active': active}))

    def test_named_file_only_and_protected_separation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'DATABASE_URL': 'postgresql://other@ep-production.neon.tech/neondb?sslmode=require'}):
            path = self.config_file(directory)
            self.assertEqual(URL, executor.read_config(path)['url'])
            for changes in (
                {'development': 'br-production'},
                {'disposable': 'br-development'},
                {'url': 'postgresql://fixture@ep-production.neon.tech/neondb?sslmode=require'},
                {'url': 'postgresql://fixture@ep-disposable.neon.tech/neondb?sslmode=require'},
                {'url': URL + '&hostaddr=192.0.2.42'},
                {'url': URL.replace('sslmode=require', 'sslmode=disable')},
                {'development_endpoint': 'ep-production'},
            ):
                with self.subTest(changes=changes):
                    with self.assertRaises(executor.DevelopmentSourceError):
                        executor.read_config(self.config_file(directory, **changes))

    def test_missing_url_and_environment_override_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.config_file(directory, url='')
            with self.assertRaises(executor.DevelopmentSourceError):
                executor.read_config(path)
            path = self.config_file(directory)
            with patch.dict(os.environ, {'PGHOSTADDR': '192.0.2.42'}):
                with self.assertRaises(executor.DevelopmentSourceError):
                    executor.read_config(path)

    def test_default_preflight_is_read_only_and_performs_no_migration(self):
        state = State()
        modes = []
        def connection(_, *, read_only):
            modes.append(read_only)
            return Connection(state)
        tls_a, tls_b = self.tls()
        with tls_a, tls_b, patch.object(executor, '_pilot_state', return_value=('pilots',)):
            result = executor.run(config_reader=lambda: CONFIG, connection=connection)
        self.assertEqual([True], modes)
        self.assertEqual('read_only_preflight', result['mode'])
        self.assertFalse(result['writes_performed'])
        self.assertFalse(state.migrated)
        self.assertFalse(any(query.startswith('INSERT') for query, _, _ in state.calls))

    def test_missing_or_wrong_confirmation_stops_before_connection(self):
        connection = Mock(side_effect=AssertionError('No connection allowed'))
        for confirmed in (None, 'br-other', 'br-production', 'br-disposable'):
            with self.subTest(confirmed=confirmed):
                with self.assertRaises(executor.DevelopmentSourceError):
                    executor.run(apply=True, confirm_branch_id=confirmed,
                                 config_reader=lambda: CONFIG, connection=connection)
        connection.assert_not_called()

    def test_endpoint_mismatch_and_inactive_tls(self):
        for host, active in (('ep-other.neon.tech', True), ('ep-development.neon.tech', False)):
            with self.subTest(host=host, tls=active):
                state = State()
                tls_a, tls_b = self.tls(active)
                with tls_a, tls_b, patch.object(executor, '_pilot_state', return_value=('pilots',)):
                    with self.assertRaises(ValueError):
                        executor.run(config_reader=lambda: CONFIG,
                                     connection=lambda *_, **__: Connection(state, host=host))
                self.assertFalse(state.migrated)

    def test_wrong_foundation_checksum_or_existing_002_blocks_write(self):
        for ledger in ({'001_foundation.sql': '0' * 64},
                       {'001_foundation.sql': FOUNDATION, executor.MIGRATION: SOURCE_HASH}):
            with self.subTest(ledger=tuple(ledger)):
                state = State()
                state.ledger = ledger
                tls_a, tls_b = self.tls()
                with tls_a, tls_b, patch.object(executor, '_pilot_state', return_value=('pilots',)):
                    with self.assertRaises(ValueError):
                        executor.run(apply=True, confirm_branch_id='br-development',
                                     config_reader=lambda: CONFIG,
                                     connection=lambda *_, **__: Connection(state))
                self.assertFalse(state.migrated)

    def test_missing_or_altered_pilot_blocks_migration(self):
        state = State()
        tls_a, tls_b = self.tls()
        with tls_a, tls_b, patch.object(executor, '_pilot_state', side_effect=ValueError('Pilot mismatch')):
            with self.assertRaises(ValueError):
                executor.run(apply=True, confirm_branch_id='br-development',
                             config_reader=lambda: CONFIG,
                             connection=lambda *_, **__: Connection(state))
        self.assertFalse(state.migrated)
        with tls_a, tls_b, patch.object(executor, '_pilot_state', side_effect=[('original',), ('altered',)]):
            with self.assertRaises(executor.DevelopmentSourceError):
                executor.run(apply=True, confirm_branch_id='br-development',
                             config_reader=lambda: CONFIG,
                             connection=lambda *_, **__: Connection(state))
        self.assertFalse(state.migrated)

    def test_real_pilot_guard_rejects_missing_or_altered_review_state(self):
        pilots = load_pilots()
        dossier_rows = [(p.record['dossier_id'], p.record['kind']) for p in pilots]
        revision_rows = [(p.record['dossier_id'], 'fixture-revision', 1, None,
                          p.content_hash, 'pending_review', 'private_preview_only') for p in pilots]
        identity_rows = [(item['institution'], item['namespace'], str(item['value']),
                          p.record['dossier_id']) for p in pilots
                         for item in p.record['official_identities']]
        class Rows:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows
        class PilotConnection:
            def __init__(self, dossiers, revisions):
                self.dossiers, self.revisions = dossiers, revisions
            def execute(self, query):
                if 'FROM ndq.dossiers' in query: return Rows(self.dossiers)
                if 'FROM ndq.dossier_revisions' in query: return Rows(self.revisions)
                if 'FROM ndq.official_identities' in query: return Rows(identity_rows)
                raise AssertionError('Unexpected pilot query')
        self.assertEqual(3, len(executor._pilot_state(PilotConnection(dossier_rows, revision_rows))[0]))
        with self.assertRaises(ValueError):
            executor._pilot_state(PilotConnection(dossier_rows[:2], revision_rows))
        altered = revision_rows.copy()
        altered[0] = (*altered[0][:5], 'approved_private', altered[0][6])
        with self.assertRaises(ValueError):
            executor._pilot_state(PilotConnection(dossier_rows, altered))

    def test_exactly_one_schema_migration_without_source_or_editorial_insert(self):
        self.assertNotIn('INSERT INTO', SOURCE_SQL.upper())
        state = State()
        modes = []
        def connection(_, *, read_only):
            modes.append(read_only)
            return Connection(state)
        tls_a, tls_b = self.tls()
        with tls_a, tls_b, patch.object(executor, '_pilot_state', return_value=('pilots',)):
            result = executor.run(apply=True, confirm_branch_id='br-development',
                                  config_reader=lambda: CONFIG, connection=connection)
        self.assertEqual([True, False, True], modes)
        self.assertEqual([executor.MIGRATION], result['applied_migrations'])
        self.assertEqual(1, sum(query == SOURCE_SQL for query, _, _ in state.calls))
        self.assertEqual(1, sum(query.startswith('INSERT INTO public.ndq_schema_migrations')
                                for query, _, _ in state.calls))
        self.assertFalse(any(query.startswith('INSERT INTO ndq.') for query, _, _ in state.calls))
        self.assertEqual({'001_foundation.sql': FOUNDATION, executor.MIGRATION: SOURCE_HASH}, state.ledger)
        self.assertEqual(0, result['source_observations_ingested'])
        self.assertEqual(0, result['checks']['source_rows'])
        self.assertTrue(result['checks']['editorial_and_publication_unchanged'])

    def test_nonempty_source_tables_or_missing_constraint_blocks_verification(self):
        state = State()
        state.migrated = True
        state.ledger[executor.MIGRATION] = SOURCE_HASH
        state.source_counts['source_snapshots'] = 1
        with self.assertRaises(executor.DevelopmentSourceError):
            executor._verify_migrated(Connection(state), ('pilots',))
        state.source_counts['source_snapshots'] = 0
        with patch.object(executor, 'SOURCE_CONSTRAINTS', {'source_snapshots': (1, 0, 0, 0)}):
            with self.assertRaises(executor.DevelopmentSourceError):
                executor._verify_migrated(Connection(state), ('pilots',))

    def test_cli_error_output_is_sanitized_and_apply_requires_confirmation(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                executor.main(['--apply'])
        self.assertEqual(2, caught.exception.code)
        with patch.object(executor, 'run', side_effect=RuntimeError('password=secret-value')):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(1, executor.main([]))
            self.assertNotIn('secret-value', output.getvalue())
            self.assertIn('"partial_changes_possible": false', output.getvalue())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(1, executor.main(['--apply', '--confirm-branch-id', 'br-development']))
            self.assertNotIn('secret-value', output.getvalue())
            self.assertIn('"partial_changes_possible": true', output.getvalue())


if __name__ == '__main__':
    unittest.main()
