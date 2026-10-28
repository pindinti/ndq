"""Stable-identity baselines. The database path has SELECT/SHOW/SET only."""
from __future__ import annotations

import re

from tools.persistence.check_connection import client_tls_details
from tools.persistence.contract import digest, load_pilots
from tools.persistence.initialize_development_neon import read_config
from tools.persistence.repository import read_record


def has_identity(record: dict, proposition_id: int) -> bool:
    return any((x.get('institution'), x.get('namespace'), str(x.get('value')))
               == ('camara', 'proposicoes', str(proposition_id)) for x in record.get('official_identities', []))


def local_baseline(proposition_id: int) -> dict:
    matches = [p for p in load_pilots() if has_identity(p.record, proposition_id)]
    if len(matches) != 1:
        return {'status': 'conflicting' if matches else 'not_found', 'record': None, 'history': []}
    return {'status': 'matched', 'record': matches[0].record, 'history': [],
            'sha256': matches[0].content_hash, 'history_complete': False,
            'review_state': None, 'source': 'local_validated_record'}


def _connect(config):
    import psycopg
    return psycopg.connect(config['url'], sslmode=config['sslmode'],
                           options='-c default_transaction_read_only=on -c statement_timeout=15000',
                           autocommit=True, connect_timeout=10, prepare_threshold=None,
                           application_name='observatorio-ndq-camara-readonly')


def database_baseline(proposition_id: int, *, config_reader=read_config, connect=_connect) -> dict:
    try:
        # This existing guard rejects production/disposable IDs and endpoints,
        # process URL conflicts and libpq target overrides before any connection.
        config = config_reader()
        with connect(config) as conn:
            if client_tls_details(conn)['tls_active'] is not True:
                raise ValueError('TLS unavailable')
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                if conn.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
                    raise ValueError('Read-only unavailable')
                if conn.execute('SELECT current_database()').fetchone()[0] != 'neondb':
                    raise ValueError('Unexpected database')
                owners = conn.execute('''SELECT dossier_id FROM ndq.official_identities
                    WHERE institution = %s AND namespace = %s AND value = %s''',
                    ('camara', 'proposicoes', str(proposition_id))).fetchall()
                if len(owners) != 1:
                    return {'status': 'conflicting' if owners else 'not_found', 'record': None, 'history': []}
                rows = conn.execute('''SELECT revision_id, revision_number, content_hash, source_observed_at
                    FROM ndq.dossier_revisions WHERE dossier_id = %s
                    ORDER BY revision_number DESC LIMIT 21''', (owners[0][0],)).fetchall()
                if not rows:
                    raise ValueError('Identity has no revision')
                records = [read_record(conn, row[0]) for row in rows[:20]]
                if any(not has_identity(record, proposition_id) or record['dossier_id'] != owners[0][0]
                       for record in records):
                    return {'status': 'conflicting', 'record': None, 'history': []}
                review = conn.execute('SELECT review_state FROM ndq.editorial_reviews WHERE revision_id = %s',
                                      (rows[0][0],)).fetchone()
                return {'status': 'matched', 'source': 'neon_development_read_only', 'record': records[0],
                        'sha256': digest(records[0]), 'history': records[1:], 'history_complete': len(rows) <= 20,
                        'revisions_read': len(records), 'latest_revision_number': rows[0][1],
                        'review_state': review[0] if review else None,
                        'read_only': True, 'tls_active': True,
                        'certificate_validation': 'not_independently_observed',
                        'branch_id_verified_by_neon_api': False}
    except Exception as exc:
        result = {'status': 'unavailable', 'record': None, 'history': [], 'error_type': type(exc).__name__}
        sqlstate = getattr(exc, 'sqlstate', None)
        if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate):
            result['sqlstate'] = sqlstate
        return result


def summary(baseline: dict) -> dict:
    out = {k: v for k, v in baseline.items() if k not in ('record', 'history')}
    record = baseline.get('record')
    if record:
        out.update(dossier_id=record['dossier_id'], slug=record['slug'],
                   last_observed_at=record['last_observed_at'], event_count=len(record['events']),
                   publication_state=record['publication_status'],
                   origin_editorial_status=record['editorial_assessment']['review_status'])
    return out
