r"""Inspect one Neon connection without printing credentials or writing to the DB.

Loads DATABASE_URL from an explicit environment or the repository root .env:
    .\.venv\Scripts\python.exe -B -m tools.persistence.check_connection
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import parse_qs, urlsplit

from .db import ConfigurationError, load_database_url


def client_tls_details(conn) -> dict:
    """Inspect only client libpq state; never infer it from the backend view."""
    details = {'tls_active': None, 'tls_evidence': 'client_attribute_unavailable',
               'tls_protocol': None, 'client_ssl_attributes_available': False,
               'certificate_validation': 'not_independently_observed'}
    try:
        pgconn = conn.pgconn
        in_use = pgconn.ssl_in_use
    except (AttributeError, NotImplementedError):
        return details
    except Exception:
        # Some libpq wrappers may raise NotSupportedError for SSL inspection.
        return details
    if not isinstance(in_use, (bool, int)):
        return details
    details['tls_active'] = bool(in_use)
    details['tls_evidence'] = 'libpq_client_ssl_in_use'
    try:
        attribute = getattr(pgconn, 'ssl_attribute', None)
    except Exception:
        return details
    if not callable(attribute):
        return details
    details['client_ssl_attributes_available'] = True
    if not in_use:
        return details
    try:
        protocol = attribute('protocol')
    except TypeError:
        try:
            protocol = attribute(b'protocol')
        except Exception:
            return details
    except Exception:
        return details
    if isinstance(protocol, bytes):
        protocol = protocol.decode('ascii', errors='ignore')
    # Whitelist version syntax so an unexpected library value cannot leak data.
    if isinstance(protocol, str) and re.fullmatch(r'TLSv?1\.[0-9]', protocol):
        details['tls_protocol'] = protocol
    return details


def check() -> dict:
    result: dict = {'database_url_available': False, 'tls_required': False,
                    'connected': False, 'read_only': False}
    try:
        url = load_database_url()
    except ConfigurationError as exc:
        result['diagnosis'] = str(exc)
        return result
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['diagnosis'] = 'Database configuration could not be loaded'
        return result
    result['database_url_available'] = True
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname:
            result['diagnosis'] = 'DATABASE_URL is not a PostgreSQL URL with a host'
            return result
        if not parsed.hostname.lower().endswith('.neon.tech'):
            result['diagnosis'] = 'Endpoint is not a recognized Neon host; connection refused'
            return result
        ssl_modes = parse_qs(parsed.query).get('sslmode', [])
        if len(ssl_modes) > 1 or (ssl_modes and ssl_modes[0] not in ('require', 'verify-ca', 'verify-full')):
            result['diagnosis'] = 'Connection URL requests an unsupported or weak TLS mode'
            return result
        ssl_mode = ssl_modes[0] if ssl_modes else 'require'
        result['tls_required'] = True
        result['sslmode_required'] = ssl_mode
    except (ValueError, TypeError):
        result['diagnosis'] = 'DATABASE_URL could not be parsed'
        return result

    try:
        import psycopg
    except ImportError:
        result['diagnosis'] = 'psycopg is unavailable in this Python environment'
        return result

    try:
        # Startup GUC applies before any query, including the first SELECT.
        # URI credentials and host are never interpolated into SQL or output.
        with psycopg.connect(url, sslmode=ssl_mode,
                             options='-c default_transaction_read_only=on',
                             autocommit=True, connect_timeout=10,
                             application_name='observatorio-ndq-readiness',
                             prepare_threshold=None) as conn:
            result.update(client_tls_details(conn))
            with conn.transaction():
                read_only = conn.execute('SHOW transaction_read_only').fetchone()[0]
                result['read_only'] = read_only == 'on'
                if not result['read_only']:
                    result['diagnosis'] = 'Server did not establish a read-only transaction'
                    return result
                if result['tls_active'] is False:
                    result['diagnosis'] = 'Client libpq reports no TLS for this connection'
                    return result
                database, schema, version = conn.execute(
                    'SELECT current_database(), current_schema(), version()').fetchone()
                result.update(current_database=database, current_schema=schema,
                              postgres_version=version)
                app_schema, ledger, dossiers, revisions = conn.execute('''SELECT
                    to_regnamespace('ndq') IS NOT NULL,
                    to_regclass('public.ndq_schema_migrations') IS NOT NULL,
                    to_regclass('ndq.dossiers') IS NOT NULL,
                    to_regclass('ndq.dossier_revisions') IS NOT NULL''').fetchone()
                result['database_state'] = {
                    'ndq_schema_exists': app_schema,
                    'migration_table_exists': ledger,
                    'dossiers_table_exists': dossiers,
                    'dossier_revisions_table_exists': revisions,
                }
                if ledger:
                    result['database_state']['migration_001_recorded'] = bool(conn.execute(
                        '''SELECT EXISTS (SELECT 1 FROM public.ndq_schema_migrations
                           WHERE name = %s)''', ('001_foundation.sql',)).fetchone()[0])
                result['connected'] = True
                if result['tls_active'] is None:
                    result['diagnosis'] = 'Client TLS status is unknown: libpq SSL inspection unavailable'
    except Exception as exc:
        # libpq exception text may contain the URL or a credential-bearing host.
        # Report only the exception class and SQLSTATE, never str(exc).
        result['error_type'] = type(exc).__name__
        sqlstate = getattr(exc, 'sqlstate', None)
        if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate):
            result['sqlstate'] = sqlstate
        result['diagnosis'] = 'Connection or read-only query failed; see error type/SQLSTATE'
    return result


def main() -> int:
    result = check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['connected'] and result['read_only'] and result.get('tls_active') else 1


if __name__ == '__main__':
    sys.exit(main())
