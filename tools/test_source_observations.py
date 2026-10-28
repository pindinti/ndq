"""Offline source-writer contract tests. No database or network access."""
from __future__ import annotations

import copy
import ast
import os
import re
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from tools.collectors.camara.ingestion_plan import plan_source_ingestion, source_content_key
from tools.persistence.source_observations import (
    SourceObservationError, validate_source_observation, write_local_test_observation)
from tools.persistence import source_observations
from tools.persistence.preflight_source_disposable_neon import inspect, SourcePreflightError
from tools.persistence.db import migrate
from tools.test_camara_collector import fixture_data, observation

CONCURRENCY_URL = os.environ.get('NDQ_SOURCE_CONCURRENCY_TEST_URL', '')


class SourceWriterOfflineTest(unittest.TestCase):
    def test_every_source_insert_column_placeholder_and_parameter_count(self):
        source = Path(source_observations.__file__).read_text(encoding='utf-8')
        inserts = set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != 'execute' or len(node.args) != 2:
                continue
            sql, bound = node.args
            if not isinstance(sql, ast.Constant) or not isinstance(sql.value, str):
                continue
            if not sql.value.lstrip().startswith('INSERT INTO ndq.'):
                continue
            match = re.search(r'INSERT INTO ndq\.(\w+)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)',
                              sql.value, re.DOTALL)
            self.assertIsNotNone(match)
            self.assertIsInstance(bound, ast.Tuple)
            table, columns, values = match.groups()
            inserts.add(table)
            column_count = len(columns.split(','))
            placeholder_count = values.count('%s')
            # The writer's identity tuple is (institution, namespace, official_id,
            # projection_version); any other starred expression needs review.
            parameter_count = 0
            for item in bound.elts:
                if isinstance(item, ast.Starred):
                    self.assertIsInstance(item.value, ast.Name)
                    self.assertEqual('identity', item.value.id)
                    parameter_count += 4
                else:
                    parameter_count += 1
            with self.subTest(table=table):
                self.assertEqual(column_count, placeholder_count)
                self.assertEqual(column_count, parameter_count)
        self.assertEqual({'source_snapshots', 'source_snapshot_items',
                          'source_collection_attempts', 'source_attempt_items',
                          'source_change_candidates'}, inserts)

    def test_selected_source_roundtrip_and_zero_related_endpoint(self):
        source = observation()
        checked = validate_source_observation(source)
        self.assertEqual(source_content_key(source), source_content_key(checked))
        self.assertEqual([], checked['relationships'])
        self.assertEqual(0, checked['coverage']['relationships']['returned_count'])
        self.assertIsNone(checked['events'][0]['occurred_at'])
        self.assertEqual('unresolved', checked['authors'][0]['official_identity']['link_to_legacy_actor'])

    def test_untrusted_or_tampered_projection_is_rejected(self):
        source = observation()
        edits = (
            lambda x: x['events'][0].update(fingerprint='0' * 64),
            lambda x: x['events'][0]['original_fields'].update(raw_document='must drop'),
            lambda x: x['coverage']['relationships'].update(complete='yes'),
            lambda x: x['coverage']['relationships'].update(returned_count=1),
            lambda x: x['authors'][0]['official_identity'].update(link_to_legacy_actor='merged'),
            lambda x: x.update(projection_version='unreviewed-projection'),
        )
        for edit in edits:
            with self.subTest(edit=edit):
                invalid = copy.deepcopy(source)
                edit(invalid)
                with self.assertRaises(SourceObservationError):
                    validate_source_observation(invalid)

    def test_repeated_and_returned_content_keys_are_deterministic(self):
        first = observation()
        data = fixture_data()
        data['events'][0]['despacho'] = 'Synthetic correction'
        changed = observation(data)
        final = observation()
        with ThreadPoolExecutor(max_workers=2) as pool:
            keys = list(pool.map(source_content_key, (first, final)))
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], source_content_key(changed))
        self.assertEqual('already_observed', plan_source_ingestion(final,
            known_content_keys=(keys[0], source_content_key(changed)), previous=first)['status'])

    def test_partial_is_never_factual_baseline(self):
        partial = observation(complete=False)
        checked = validate_source_observation(partial)
        self.assertFalse(checked['coverage']['events']['complete'])
        self.assertEqual('incomplete_or_invalid', plan_source_ingestion(checked)['status'])
        data = fixture_data()
        data['proposition']['dataApresentacao'] = 'unknown source date'
        malformed = validate_source_observation(observation(data))
        self.assertFalse(malformed['coverage']['proposition']['complete'])
        self.assertIn({'code': 'presentation_date_precision_unknown'},
                      malformed['coverage']['proposition']['normalization_issues'])

    def test_write_is_disabled_before_target_access(self):
        class Connection:
            @property
            def info(self):
                raise AssertionError('Target must not be inspected')
        with self.assertRaisesRegex(SourceObservationError, 'disabled'):
            write_local_test_observation(Connection(), observation())

    def test_nonlocal_target_is_refused_before_transaction(self):
        class Info:
            def get_parameters(self):
                return {'host': 'example.invalid', 'dbname': 'ndq_test'}
        class Connection:
            info = Info()
            def execute(self, *_):
                raise AssertionError('No SQL may run for a nonlocal target')
        with self.assertRaisesRegex(SourceObservationError, 'localhost ndq_test'):
            write_local_test_observation(Connection(), observation(), allow_local_test=True)

    def test_driver_failure_message_is_sanitized(self):
        class Info:
            def get_parameters(self):
                return {'host': 'localhost', 'dbname': 'ndq_test'}
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchone(self): return self.rows[0]
            def fetchall(self): return self.rows
        class Connection:
            info = Info()
            def execute(self, sql, *_):
                if 'current_database()' in sql: return Result([('ndq_test',)])
                return Result([('002_source_observations.sql',)])
            def transaction(self):
                raise RuntimeError('password=must-not-appear')
        with self.assertRaises(SourceObservationError) as caught:
            write_local_test_observation(Connection(), observation(), allow_local_test=True)
        self.assertNotIn('must-not-appear', str(caught.exception))

    def test_migration_is_additive_and_writer_has_no_publication_sql(self):
        migration = (Path(__file__).parent / 'persistence' / 'migrations' /
                     '002_source_observations.sql').read_text(encoding='utf-8').lower()
        self.assertIn('unique (institution, namespace, official_id, projection_version, content_hash)', migration)
        self.assertIn('source_collection_attempts', migration)
        self.assertIn('source_change_candidates', migration)
        self.assertIn('source_editorial_decisions', migration)
        self.assertNotIn('alter table ndq.dossier_revisions', migration)
        writer = (Path(__file__).parent / 'persistence' / 'source_observations.py').read_text(encoding='utf-8').lower()
        self.assertNotIn('insert into ndq.dossier_revisions', writer)
        self.assertNotIn('insert into ndq.publication_states', writer)
        self.assertNotIn('insert into ndq.source_editorial_decisions', writer)

    def test_legacy_initializer_can_apply_only_001(self):
        calls = []
        class Result:
            def fetchall(self): return []
        class Connection:
            def transaction(self): return nullcontext()
            def execute(self, query, params=(), **kwargs):
                calls.append(query)
                return Result()
        self.assertEqual(['001_foundation.sql'], migrate(Connection(), through=1))
        self.assertFalse(any('CREATE TABLE ndq.source_snapshots' in str(query) for query in calls))

    def test_disposable_source_preflight_is_read_only(self):
        queries = []
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchone(self): return self.rows[0]
            def fetchall(self): return self.rows
        class Connection:
            def transaction(self): return nullcontext()
            def execute(self, query, params=()):
                queries.append(query)
                if query.startswith('SET '): return Result([])
                if query.startswith('SHOW '): return Result([('on',)])
                if 'current_database()' in query: return Result([('neondb',)])
                if 'to_regnamespace' in query: return Result([('ndq', 'public.ndq_schema_migrations')])
                if 'to_regclass' in query: return Result([(None,)])
                if query.startswith('SELECT name'):
                    import hashlib
                    foundation = (Path(__file__).parent / 'persistence' / 'migrations' /
                                  '001_foundation.sql').read_text(encoding='utf-8')
                    return Result([('001_foundation.sql', hashlib.sha256(foundation.encode()).hexdigest())])
                raise AssertionError('Unexpected preflight SQL')
        with patch('tools.persistence.preflight_source_disposable_neon.client_tls_details',
                   return_value={'tls_active': True}):
            result = inspect(Connection())
        self.assertTrue(result['migration_001_verified'])
        self.assertFalse(result['writes_performed'])
        self.assertTrue(all(query.split()[0] in ('SET', 'SHOW', 'SELECT') for query in queries))

    def test_preflight_rejects_wrong_migration_state(self):
        class Result:
            def __init__(self, row): self.row = row
            def fetchone(self): return self.row
        class Connection:
            def transaction(self): return nullcontext()
            def execute(self, query, *_):
                if query.startswith('SET '): return Result(())
                if query.startswith('SHOW '): return Result(('on',))
                if 'current_database()' in query: return Result(('neondb',))
                return Result((None, None))
        with patch('tools.persistence.preflight_source_disposable_neon.client_tls_details',
                   return_value={'tls_active': True}):
            with self.assertRaises(SourcePreflightError):
                inspect(Connection())


@unittest.skipUnless(CONCURRENCY_URL, 'Opt-in concurrency requires pre-migrated disposable localhost ndq_test')
class SourceConcurrencyPostgresTest(unittest.TestCase):
    """Two committed local transactions; never runs in the rollback-only class."""

    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        try:
            cfg = conninfo_to_dict(CONCURRENCY_URL)
        except Exception:
            raise ValueError('Invalid local concurrency test configuration') from None
        if (cfg.get('host') not in ('localhost', '127.0.0.1', '::1')
                or cfg.get('dbname') != 'ndq_test'
                or any(cfg.get(key) for key in ('hostaddr', 'service'))):
            raise ValueError('Concurrent source test requires explicit localhost ndq_test')
        from tools.persistence.db import connect
        try:
            with connect(CONCURRENCY_URL) as conn:
                if conn.execute('SELECT current_database()').fetchone()[0] != 'ndq_test':
                    raise ValueError('Unexpected concurrent-test database')
                applied = [r[0] for r in conn.execute('SELECT name FROM public.ndq_schema_migrations').fetchall()]
                if '002_source_observations.sql' not in applied:
                    raise ValueError('Concurrent source test requires pre-applied migration 002')
        except Exception:
            raise ValueError('Local concurrent-test database is unavailable or not prepared') from None

    def test_simultaneous_identical_source_creates_one_snapshot(self):
        from uuid import uuid4
        from tools.collectors.http import BASE, FetchResult
        from tools.collectors.camara.normalize import normalize
        from tools.persistence.db import connect
        pid = 100_000_000 + (uuid4().int % 800_000_000)
        data = fixture_data()
        data['proposition']['id'] = pid
        data['proposition']['uri'] = f'{BASE}/proposicoes/{pid}'
        suffixes = {'proposition': '', 'events': '/tramitacoes',
                    'authors': '/autores', 'relationships': '/relacionadas'}
        fetched = {name: FetchResult(f'{BASE}/proposicoes/{pid}{suffix}', data[name], True,
                  [{'url': f'{BASE}/proposicoes/{pid}{suffix}', 'retrieved_at': '2026-10-27T14:00:00Z'}])
                  for name, suffix in suffixes.items()}
        source = normalize(pid, fetched, '2026-10-27T14:00:00Z')
        barrier = threading.Barrier(2)
        def worker():
            try:
                with connect(CONCURRENCY_URL) as conn:
                    barrier.wait(timeout=15)
                    return write_local_test_observation(conn, source, allow_local_test=True)
            except Exception:
                raise AssertionError('Local concurrent source writer failed') from None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual([False, True], sorted(x['snapshot_created'] for x in results))
        self.assertEqual(['initial', 'unchanged'], sorted(x['transition'] for x in results))
        try:
            with connect(CONCURRENCY_URL) as conn:
                counts = conn.execute('''SELECT
                    (SELECT count(*) FROM ndq.source_snapshots WHERE official_id = %s),
                    (SELECT count(*) FROM ndq.source_collection_attempts WHERE official_id = %s),
                    (SELECT count(*) FROM ndq.source_change_candidates WHERE official_id = %s)''',
                    (str(pid), str(pid), str(pid))).fetchone()
        except Exception:
            raise AssertionError('Local concurrent source verification failed') from None
        self.assertEqual((1, 2, 0), counts)


if __name__ == '__main__':
    unittest.main()
