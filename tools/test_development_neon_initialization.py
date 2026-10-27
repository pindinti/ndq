"""Offline guards for the persistent development initializer; no Neon access."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.persistence import initialize_development_neon as v


class DevelopmentConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.guard = self.root / '.env.ndq-development'
        self.dev = self.root / '.env'
        self.values = {
            'NDQ_DEVELOPMENT_BRANCH_ID': 'br-development',
            'NDQ_DEVELOPMENT_ENDPOINT_ID': 'ep-development',
            'NDQ_PRODUCTION_BRANCH_ID': 'br-production',
            'NDQ_PRODUCTION_ENDPOINT_ID': 'ep-production',
            'NDQ_DISPOSABLE_BRANCH_ID': 'br-disposable',
            'NDQ_DISPOSABLE_ENDPOINT_ID': 'ep-disposable',
        }
        self.url = 'postgresql://dev:fake-password@ep-development.region.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

    def write(self):
        self.guard.write_text(''.join(f'{key}={value}\n' for key, value in self.values.items()), encoding='utf-8')
        self.dev.write_text('DATABASE_URL=' + self.url + '\n', encoding='utf-8')

    def read(self):
        with patch.dict('os.environ', {'DATABASE_URL': '', 'PGHOSTADDR': '',
                                      'PGSERVICE': '', 'PGOPTIONS': ''}):
            return v.read_config(self.root)

    def test_requires_named_guard_and_root_development_url(self):
        with self.assertRaises(v.TargetError):
            self.read()
        self.guard.write_text('', encoding='utf-8')
        with self.assertRaises(v.TargetError):
            self.read()

    def test_accepts_direct_development_url_and_never_reads_disposable_url(self):
        self.write()
        (self.root / '.env.ndq-validation').write_text('NDQ_DISPOSABLE_DATABASE_URL=malformed\n', encoding='utf-8')
        got = self.read()
        self.assertEqual('br-development', got['branch_id'])
        self.assertEqual('require', got['sslmode'])
        self.assertEqual(self.url, got['url'])

    def test_rejects_production_and_disposable_branch_ids(self):
        for protected in ('NDQ_PRODUCTION_BRANCH_ID', 'NDQ_DISPOSABLE_BRANCH_ID'):
            with self.subTest(protected=protected):
                self.values['NDQ_DEVELOPMENT_BRANCH_ID'] = self.values[protected]
                self.write()
                with self.assertRaises(v.TargetError):
                    self.read()
        self.values['NDQ_DEVELOPMENT_BRANCH_ID'] = 'br-development'

    def test_rejects_production_and_disposable_endpoints_before_connection(self):
        for protected in ('NDQ_PRODUCTION_ENDPOINT_ID', 'NDQ_DISPOSABLE_ENDPOINT_ID'):
            with self.subTest(protected=protected):
                self.values['NDQ_DEVELOPMENT_ENDPOINT_ID'] = self.values[protected]
                self.write()
                with self.assertRaises(v.TargetError):
                    self.read()

    def test_rejects_url_target_that_differs_from_declared_development_endpoint(self):
        self.write()
        self.url = self.url.replace('ep-development.', 'ep-production.')
        self.write()
        with self.assertRaises(v.TargetError):
            self.read()

    def test_rejects_pooler_weak_tls_and_host_override(self):
        for changed in (self.url.replace('ep-development.', 'ep-development-pooler.'),
                        self.url.replace('sslmode=require', 'sslmode=disable'),
                        self.url + '&hostaddr=127.0.0.1'):
            with self.subTest():
                self.url = changed
                self.write()
                with self.assertRaises(v.TargetError):
                    self.read()

    def test_rejects_conflicting_process_url_and_libpq_override(self):
        self.write()
        with patch.dict('os.environ', {'DATABASE_URL': 'postgresql://other',
                                      'PGHOSTADDR': '', 'PGSERVICE': '', 'PGOPTIONS': ''}):
            with self.assertRaises(v.TargetError):
                v.read_config(self.root)
        with patch.dict('os.environ', {'DATABASE_URL': '', 'PGHOSTADDR': '127.0.0.1',
                                      'PGSERVICE': '', 'PGOPTIONS': ''}):
            with self.assertRaises(v.TargetError):
                v.read_config(self.root)

    def test_manual_branch_confirmation_required_before_any_connection(self):
        with patch.object(v, 'read_config', return_value={'branch_id': 'br-development'}), \
             patch.object(v, '_connection') as connect:
            with self.assertRaises(v.TargetError):
                v.run(apply=True, confirm_branch_id='br-production')
            connect.assert_not_called()

    def test_preflight_uses_read_only_connection_and_never_calls_migration(self):
        class Result:
            def fetchone(self):
                return ('on',)

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def transaction(self):
                return contextlib.nullcontext()

            def execute(self, query):
                if query != 'SHOW transaction_read_only':
                    raise AssertionError('Unexpected preflight query')
                return Result()

        config = {'branch_id': 'br-development'}
        with patch.object(v, 'read_config', return_value=config), \
             patch.object(v, 'load_pilots', return_value=[object()] * 3), \
             patch.object(v, '_connection', return_value=Connection()) as connect, \
             patch.object(v, '_inspect_empty_target', return_value={'database': 'neondb'}), \
             patch.object(v, 'migrate') as migrate, \
             patch.object(v, 'import_pilots') as import_pilots:
            result = v.run(apply=False)
        connect.assert_called_once_with(config, read_only=True)
        migrate.assert_not_called()
        import_pilots.assert_not_called()
        self.assertFalse(result['writes_performed'])

    def test_tls_and_empty_schema_required(self):
        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def execute(self, query):
                if 'current_database()' in query:
                    return Result(('neondb',))
                return Result(('ndq', None))

        with patch.object(v, 'client_tls_details', return_value={'tls_active': False}):
            with self.assertRaises(v.TargetError):
                v._inspect_empty_target(Connection())
        with patch.object(v, 'client_tls_details', return_value={'tls_active': True}):
            with self.assertRaises(v.TargetError):
                v._inspect_empty_target(Connection())

    def test_error_output_omits_credentials_and_driver_message(self):
        output = io.StringIO()
        with patch.object(v, 'run', side_effect=RuntimeError('password=hidden host=private')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(1, v.main(['--apply', '--confirm-branch-id', 'br-development']))
        self.assertNotIn('hidden', output.getvalue())
        self.assertNotIn('private', output.getvalue())
        self.assertIn('partial_changes_possible', output.getvalue())


if __name__ == '__main__':
    unittest.main()
