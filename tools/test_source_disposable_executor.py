"""Offline guard and orchestration tests; no Neon connection or SQL writes."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

from tools.collectors.camara.compare import compare_snapshots
from tools.collectors.camara.ingestion_plan import source_content_key
from tools.persistence import validate_source_disposable_neon as executor
from tools.persistence.db import MIGRATIONS
from tools.persistence.contract import load_pilots
from tools.persistence.source_observations import validate_source_observation
from tools.persistence import validate_disposable_neon as disposable

CONFIG = {'url': 'postgresql://fixture@ep-disposable.neon.tech/neondb?sslmode=require',
          'branch_id': 'br-disposable', 'endpoint_id': 'ep-disposable', 'sslmode': 'require',
          'development_endpoint_verified': True}


class FakeConnection:
    def __init__(self, *, host='ep-disposable.neon.tech', database='neondb', hostaddr=None):
        self.parameters = {'host': host, 'dbname': database}
        if hostaddr is not None:
            self.parameters['hostaddr'] = hostaddr
        self.info = self
    def get_parameters(self): return self.parameters
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def transaction(self): return nullcontext()


class DisposableExecutorOfflineTest(unittest.TestCase):
    def test_synthetic_scenarios_are_selected_and_separate_from_pilots(self):
        a = executor._fixture(executor.SYNTHETIC_ID)
        b = executor._fixture(executor.SYNTHETIC_ID, variant='B')
        partial = executor._fixture(executor.SYNTHETIC_ID, events_complete=False)
        correction = executor._fixture(executor.SYNTHETIC_ID, variant='D')
        ambiguous = executor._fixture(executor.SYNTHETIC_ID, variant='F', ambiguous=True)
        self.assertNotEqual(2498778, executor.SYNTHETIC_ID)
        self.assertEqual(source_content_key(a), source_content_key(executor._fixture(executor.SYNTHETIC_ID)))
        self.assertNotEqual(source_content_key(a), source_content_key(b))
        self.assertTrue(any(f['field_path'] == 'despacho' and f['classification'] == 'changed'
                            for f in compare_snapshots(b, a)))
        self.assertTrue(any(f['reason'] == 'possible_event_date_or_agency_correction'
                            for f in compare_snapshots(correction, a)))
        self.assertTrue(any(f['classification'] == 'conflicting'
                            for f in compare_snapshots(ambiguous, correction)))
        self.assertFalse(partial['coverage']['events']['complete'])
        self.assertEqual(0, a['coverage']['relationships']['returned_count'])
        self.assertIsNone(a['events'][0]['occurred_at'])
        self.assertEqual(a['authors'][0]['official_identity']['value'],
                         validate_source_observation(a)['authors'][0]['official_identity']['value'])

    def test_apply_requires_exact_branch_before_any_connection(self):
        connect = Mock(side_effect=AssertionError('Connection attempted'))
        with self.assertRaises(executor.DisposableSourceError):
            executor.run(apply=True, confirm_branch_id='br-wrong',
                         config_reader=lambda: CONFIG, connection=connect)
        connect.assert_not_called()

    def test_default_is_read_only_and_does_not_call_migration(self):
        calls = []
        def connect(*_, read_only):
            calls.append(read_only)
            return FakeConnection()
        with patch.object(executor, 'client_tls_details', return_value={'tls_active': True}), \
             patch.object(executor, 'inspect_foundation', return_value={'migration_001_verified': True}), \
             patch.object(executor, '_pilot_state', return_value=((1, 2, 3), (), ())), \
             patch.object(executor, '_apply_only_002', side_effect=AssertionError('Write attempted')):
            result = executor.run(config_reader=lambda: CONFIG, connection=connect)
        self.assertEqual([True], calls)
        self.assertFalse(result['writes_performed'])
        self.assertEqual('read_only_preflight', result['mode'])

    def test_effective_target_and_tls_are_checked(self):
        with patch.object(executor, 'client_tls_details', return_value={'tls_active': True}):
            executor._connected_target(FakeConnection(), CONFIG)
            executor._connected_target(FakeConnection(hostaddr='192.0.2.42'), CONFIG)
            for wrong in (FakeConnection(host='ep-other.neon.tech'), FakeConnection(database='other')):
                with self.assertRaises(executor.DisposableSourceError):
                    executor._connected_target(wrong, CONFIG)
        with patch.object(executor, 'client_tls_details', return_value={'tls_active': False}):
            with self.assertRaises(executor.DisposableSourceError):
                executor._connected_target(FakeConnection(), CONFIG)

    def test_libpq_override_is_refused(self):
        with patch.dict(os.environ, {'PGHOSTADDR': '127.0.0.1'}):
            with self.assertRaises(executor.DisposableSourceError):
                executor._config_guard(CONFIG)

    def test_explicit_uri_hostaddr_is_refused_before_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env.ndq-validation'
            path.write_text('\n'.join((
                'NDQ_DISPOSABLE_DATABASE_URL=postgresql://fixture@ep-disposable.neon.tech/neondb?sslmode=require&hostaddr=192.0.2.42',
                'NDQ_DISPOSABLE_BRANCH_ID=br-disposable',
                'NDQ_DISPOSABLE_ENDPOINT_ID=ep-disposable',
                'NDQ_DEVELOPMENT_BRANCH_ID=br-development',
                'NDQ_PRODUCTION_BRANCH_ID=br-production',
                'NDQ_PRODUCTION_ENDPOINT_ID=ep-production',
            )), encoding='utf-8')
            with patch.object(disposable, 'ROOT', Path(directory)):
                with self.assertRaises(disposable.TargetError):
                    disposable.read_config(path)

    def test_connection_factory_does_not_supply_hostaddr(self):
        with patch('psycopg.connect', return_value=object()) as connect:
            disposable._connection(CONFIG['url'], CONFIG['sslmode'], read_only=True)
        self.assertNotIn('hostaddr', connect.call_args.kwargs)

    def test_only_002_is_executed_after_001_checksum(self):
        source_001 = (MIGRATIONS / '001_foundation.sql').read_text(encoding='utf-8')
        source_002 = (MIGRATIONS / executor.MIGRATION).read_text(encoding='utf-8')
        checksum = hashlib.sha256(source_001.encode()).hexdigest()
        calls = []
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows
            def fetchone(self): return self.rows[0]
        class Connection:
            def transaction(self): return nullcontext()
            def execute(self, query, params=(), **kwargs):
                calls.append((query, params, kwargs))
                if query.startswith('SELECT name'): return Result([('001_foundation.sql', checksum)])
                if 'to_regclass' in query: return Result([(None,)])
                return Result([])
        executor._apply_only_002(Connection())
        self.assertEqual(1, sum(query == source_002 for query, _, _ in calls))
        self.assertFalse(any(query == source_001 for query, _, _ in calls))
        self.assertTrue(any(query.startswith('INSERT INTO public.ndq_schema_migrations')
                            and params[0] == executor.MIGRATION for query, params, _ in calls))

    def test_migration_state_change_blocks_ddl(self):
        calls = []
        class Result:
            def fetchall(self): return [('001_foundation.sql', '0'*64)]
        class Connection:
            def transaction(self): return nullcontext()
            def execute(self, query, params=(), **kwargs):
                calls.append(query)
                return Result()
        with self.assertRaises(executor.DisposableSourceError):
            executor._apply_only_002(Connection())
        self.assertFalse(any('CREATE TABLE ndq.source_snapshots' in query for query in calls))

    def test_pilot_baseline_checks_identity_revision_review_and_publication(self):
        pilots = load_pilots()
        dossier_rows = sorted((p.record['dossier_id'], p.record['kind']) for p in pilots)
        revision_rows = sorted((p.record['dossier_id'], 'synthetic-revision', 1, None,
                                p.content_hash, 'pending_review', 'private_preview_only') for p in pilots)
        identities = sorted((item['institution'], item['namespace'], str(item['value']),
                             p.record['dossier_id']) for p in pilots for item in p.record['official_identities'])
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows
        class Connection:
            def __init__(self, revisions): self.revisions = revisions
            def execute(self, query):
                if 'FROM ndq.dossiers' in query: return Result(dossier_rows)
                if 'FROM ndq.dossier_revisions' in query: return Result(self.revisions)
                if 'FROM ndq.official_identities' in query: return Result(identities)
                raise AssertionError('Unexpected pilot SQL')
        self.assertEqual(3, len(executor._pilot_state(Connection(revision_rows))[0]))
        altered = list(revision_rows)
        altered[0] = (*altered[0][:5], 'approved_private', altered[0][6])
        with self.assertRaises(executor.DisposableSourceError):
            executor._pilot_state(Connection(altered))

    def test_cli_missing_confirmation_and_error_output_are_sanitized(self):
        with patch.object(executor, 'run', side_effect=AssertionError('run called')):
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


if __name__ == '__main__':
    unittest.main()
