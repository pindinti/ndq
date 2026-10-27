"""Read-only verifier regressions using fake libpq connections, never Neon."""
from __future__ import annotations

import json
import sys
import types
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from unittest.mock import patch

from tools.persistence import check_connection

URL = 'postgresql://user:secret-value@private-endpoint.neon.tech/dev?sslmode=verify-full'


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, pgconn):
        self.pgconn = pgconn
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def transaction(self):
        return nullcontext()

    def execute(self, query, params=None):
        self.queries.append(query)
        if 'pg_stat_ssl' in query:
            raise AssertionError('Backend SSL state must not gate client TLS')
        if query == 'SHOW transaction_read_only':
            return Result(('on',))
        if 'current_database()' in query:
            return Result(('dev', 'public', 'PostgreSQL test version'))
        if 'to_regnamespace' in query:
            return Result((False, False, False, False))
        raise AssertionError('Unexpected SQL in read-only verifier')


class ClientTlsTest(unittest.TestCase):
    def run_check(self, pgconn, url=URL):
        conn = Connection(pgconn)
        kwargs_seen = {}

        def connect(*args, **kwargs):
            kwargs_seen.update(kwargs)
            return conn

        with patch.object(check_connection, 'load_database_url', return_value=url), \
                patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connect)}):
            result = check_connection.check()
        return result, conn, kwargs_seen

    def test_libpq_true_is_not_overruled_by_backend_ssl_view(self):
        result, conn, kwargs = self.run_check(types.SimpleNamespace(ssl_in_use=True))
        self.assertTrue(result['connected'])
        self.assertTrue(result['tls_active'])
        self.assertEqual('libpq_client_ssl_in_use', result['tls_evidence'])
        self.assertIsNone(result['tls_protocol'])
        self.assertFalse(result['client_ssl_attributes_available'])
        self.assertEqual('not_independently_observed', result['certificate_validation'])
        self.assertEqual('verify-full', kwargs['sslmode'])
        self.assertIn('default_transaction_read_only=on', kwargs['options'])
        self.assertEqual('SHOW transaction_read_only', conn.queries[0])
        self.assertTrue(all('pg_stat_ssl' not in q for q in conn.queries))
        self.assertNotIn('secret-value', json.dumps(result))
        self.assertNotIn('private-endpoint', json.dumps(result))

    def test_client_protocol_is_reported_only_when_attribute_is_supported(self):
        pgconn = types.SimpleNamespace(ssl_in_use=True, ssl_attribute=lambda name: 'TLSv1.3')
        result, _, _ = self.run_check(pgconn)
        self.assertTrue(result['client_ssl_attributes_available'])
        self.assertEqual('TLSv1.3', result['tls_protocol'])

    def test_unexpected_attribute_cannot_leak_credentials(self):
        pgconn = types.SimpleNamespace(ssl_in_use=True,
                                       ssl_attribute=lambda name: 'secret-value.private-endpoint.neon.tech')
        result, _, _ = self.run_check(pgconn)
        self.assertIsNone(result['tls_protocol'])
        self.assertNotIn('secret-value', json.dumps(result))

    def test_client_no_tls_fails_even_if_sslmode_requires_it(self):
        result, conn, _ = self.run_check(types.SimpleNamespace(ssl_in_use=False))
        self.assertFalse(result['tls_active'])
        self.assertFalse(result['connected'])
        self.assertEqual(['SHOW transaction_read_only'], conn.queries)

    def test_missing_client_attribute_is_unknown_not_false_or_verified(self):
        result, _, _ = self.run_check(types.SimpleNamespace())
        self.assertIsNone(result['tls_active'])
        self.assertTrue(result['connected'])
        self.assertEqual('client_attribute_unavailable', result['tls_evidence'])
        self.assertEqual('not_independently_observed', result['certificate_validation'])
        with patch.object(check_connection, 'check', return_value=result), redirect_stdout(StringIO()):
            self.assertEqual(1, check_connection.main())

    def test_require_mode_does_not_claim_certificate_validation(self):
        result, _, kwargs = self.run_check(types.SimpleNamespace(ssl_in_use=True),
                                           url=URL.replace('verify-full', 'require'))
        self.assertEqual('require', kwargs['sslmode'])
        self.assertTrue(result['tls_active'])
        self.assertEqual('not_independently_observed', result['certificate_validation'])

    def test_weak_mode_is_rejected_before_connect(self):
        with patch.object(check_connection, 'load_database_url', return_value=URL.replace('verify-full', 'disable')), \
                patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=lambda *a, **k: (_ for _ in ()).throw(AssertionError('Must not connect')))}):
            result = check_connection.check()
        self.assertFalse(result['connected'])
        self.assertFalse(result['tls_required'])

    def test_connection_error_cannot_expose_url_or_hostname(self):
        class PrivateError(Exception):
            sqlstate = 'secret-value'

        def connect(*args, **kwargs):
            raise PrivateError('password=secret-value host=private-endpoint.neon.tech')

        with patch.object(check_connection, 'load_database_url', return_value=URL), \
                patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connect)}):
            result = check_connection.check()
        rendered = json.dumps(result)
        self.assertNotIn('secret-value', rendered)
        self.assertNotIn('private-endpoint', rendered)
        self.assertNotIn('sqlstate', result)
        self.assertEqual('PrivateError', result['error_type'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
