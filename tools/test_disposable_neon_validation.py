"""Offline safety tests; no Neon credentials or connection are used."""
from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.persistence import validate_disposable_neon as v
from tools.persistence.db import MIGRATIONS


class DisposableNeonConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / '.env.ndq-validation'
        self.values = {
            'NDQ_DISPOSABLE_DATABASE_URL': 'postgresql://test:fake-password@ep-test.region.aws.neon.tech/neondb?sslmode=require',
            'NDQ_DISPOSABLE_BRANCH_ID': 'br-test',
            'NDQ_DISPOSABLE_ENDPOINT_ID': 'ep-test',
            'NDQ_DEVELOPMENT_BRANCH_ID': 'br-development',
            'NDQ_PRODUCTION_BRANCH_ID': 'br-production',
            'NDQ_PRODUCTION_ENDPOINT_ID': 'ep-production',
        }
        self.root_patch = patch.object(v, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def write_config(self):
        self.config.write_text(''.join(f'{key}={value}\n' for key, value in self.values.items()), encoding='utf-8')

    def test_count_inventory_tracks_migration_tables(self):
        sql = (MIGRATIONS / '001_foundation.sql').read_text(encoding='utf-8')
        tables = set(re.findall(r'CREATE TABLE ndq[.]([a-z_]+)', sql))
        self.assertEqual(tables, set(v.TABLES))

    def test_requires_separate_named_file(self):
        with self.assertRaises(v.TargetError):
            v.read_config(self.config)

    def test_accepts_explicit_direct_disposable_endpoint(self):
        self.write_config()
        with patch.dict('os.environ', {'DATABASE_URL': ''}):
            got = v.read_config(self.config)
        self.assertEqual('ep-test', got['endpoint_id'])
        self.assertEqual('require', got['sslmode'])

    def test_rejects_persistent_development_endpoint_from_root_env(self):
        self.write_config()
        (self.root / '.env').write_text('DATABASE_URL=postgresql://dev:secret@ep-test.region.aws.neon.tech/neondb?sslmode=require\n', encoding='utf-8')
        with patch.dict('os.environ', {'DATABASE_URL': ''}), self.assertRaises(v.TargetError):
            v.read_config(self.config)

    def test_rejects_persistent_development_endpoint_from_process_env(self):
        self.write_config()
        with patch.dict('os.environ', {'DATABASE_URL': self.values['NDQ_DISPOSABLE_DATABASE_URL']}), self.assertRaises(v.TargetError):
            v.read_config(self.config)

    def test_rejects_production_endpoint(self):
        self.values['NDQ_PRODUCTION_ENDPOINT_ID'] = 'ep-test'
        self.write_config()
        with self.assertRaises(v.TargetError):
            v.read_config(self.config)

    def test_rejects_protected_branch_ids_even_with_distinct_endpoints(self):
        for key in ('NDQ_DEVELOPMENT_BRANCH_ID', 'NDQ_PRODUCTION_BRANCH_ID'):
            with self.subTest(key=key):
                self.values['NDQ_DISPOSABLE_BRANCH_ID'] = self.values[key]
                self.write_config()
                with self.assertRaises(v.TargetError):
                    v.read_config(self.config)

    def test_rejects_weak_tls_and_pooler(self):
        for url in ('postgresql://test:p@ep-test.region.aws.neon.tech/neondb?sslmode=disable',
                    'postgresql://test:p@ep-test-pooler.region.aws.neon.tech/neondb?sslmode=require'):
            with self.subTest(url_kind='weak' if 'disable' in url else 'pooler'):
                self.values['NDQ_DISPOSABLE_DATABASE_URL'] = url
                self.write_config()
                with self.assertRaises(v.TargetError):
                    v.read_config(self.config)

    def test_mismatched_branch_confirmation_stops_before_connection(self):
        with patch.object(v, 'read_config', return_value={'branch_id': 'br-test'}), \
             patch.object(v, '_connection') as connect:
            with self.assertRaises(v.TargetError):
                v.run(apply=True, confirm_branch_id='br-other')
            connect.assert_not_called()

    def test_preflight_refuses_missing_client_tls(self):
        class FakeConnection:
            def execute(self, *_args, **_kwargs):
                raise AssertionError('No database query expected after TLS rejection')

        with patch.object(v, 'client_tls_details', return_value={'tls_active': False}):
            with self.assertRaises(v.TargetError):
                v._inspect_target(FakeConnection(), {}, must_be_empty=True)

    def test_apply_requires_empty_ndq_state_before_migration(self):
        class FakeResult:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeConnection:
            def execute(self, query):
                if 'current_database()' in query:
                    return FakeResult(('neondb',))
                return FakeResult(('ndq', None))

        with patch.object(v, 'client_tls_details', return_value={'tls_active': True}):
            with self.assertRaises(v.TargetError):
                v._inspect_target(FakeConnection(), {}, must_be_empty=True)

    def test_output_discards_exception_text_with_credentials(self):
        buffer = io.StringIO()
        with patch.object(v, 'run', side_effect=RuntimeError('password=do-not-print host=private-host')), \
             contextlib.redirect_stdout(buffer):
            self.assertEqual(1, v.main(['--apply', '--confirm-branch-id', 'br-test']))
        output = buffer.getvalue()
        self.assertNotIn('do-not-print', output)
        self.assertNotIn('private-host', output)
        self.assertIn('partial_changes_possible', output)


if __name__ == '__main__':
    unittest.main()
