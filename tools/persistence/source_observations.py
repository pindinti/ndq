"""Narrow source writer, deliberately inaccessible from the collection CLI.

Only an explicitly opted-in localhost ndq_test connection can call the public
entry point in this phase. A later disposable-Neon wrapper needs its own reviewed
target guard and authorization. No dossier, editorial or publication table writes.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from uuid import uuid4

from tools.collectors.http import BASE, FetchResult, safe_page
from tools.collectors.camara.compare import compare_snapshots
from tools.collectors.camara.ingestion_plan import SECTIONS, source_content_key
from tools.collectors.camara.normalize import PROJECTION_VERSION, digest, normalize


class SourceObservationError(ValueError):
    pass


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
        raise SourceObservationError('UTC retrieval timestamp required')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise SourceObservationError('Invalid UTC retrieval timestamp') from None


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _endpoint(pid: int, section: str) -> str:
    return f'{BASE}/proposicoes/{pid}' + {
        'proposition': '', 'events': '/tramitacoes',
        'authors': '/autores', 'relationships': '/relacionadas'}[section]


def validate_source_observation(observation: dict) -> dict:
    """Re-normalize selected fields; return a bounded, trusted storage projection."""
    try:
        pid = observation['proposition_id']
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
            raise SourceObservationError('Invalid proposition identity')
        if observation['projection_version'] != PROJECTION_VERSION:
            raise SourceObservationError('Unsupported source projection')
        completed_at = observation['retrieved_at']
        _utc(completed_at)
        if observation['retrieval_time_scope'] != 'collection_completed_at':
            raise SourceObservationError('Unsupported observation time scope')
        if set(observation['coverage']) != set(SECTIONS):
            raise SourceObservationError('Four endpoint coverage records required')
        normalization_issues = {section: [] for section in SECTIONS}
        if not isinstance(observation.get('issues'), list) or len(observation['issues']) > 100:
            raise SourceObservationError('Invalid normalization issue count')
        for issue in observation['issues']:
            section, code = issue['section'], issue['code']
            if (section not in normalization_issues or not isinstance(code, str)
                    or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code)):
                raise SourceObservationError('Invalid normalization issue')
            normalization_issues[section].append({'code': code})
        fetched, coverage = {}, {}
        for section in SECTIONS:
            endpoint = _endpoint(pid, section)
            value = observation[section]
            rows = [value] if section == 'proposition' and value is not None else (
                [] if section == 'proposition' else value)
            if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
                raise SourceObservationError('Invalid selected item list')
            if len(rows) > 10000 or (section == 'proposition' and len(rows) > 1):
                raise SourceObservationError('Item limit exceeded')
            data = rows[0]['original_fields'] if section == 'proposition' and rows else (
                None if section == 'proposition' else [item['original_fields'] for item in rows])
            raw_coverage = observation['coverage'][section]
            complete = raw_coverage['complete']
            if not isinstance(complete, bool):
                raise SourceObservationError('Invalid endpoint completeness')
            pages = raw_coverage['pages']
            if not isinstance(pages, list) or len(pages) > 100:
                raise SourceObservationError('Invalid endpoint page count')
            safe_pages = []
            for page in pages:
                url = safe_page(page['url'], endpoint)
                _utc(page['retrieved_at'])
                safe_pages.append({'url': url, 'retrieved_at': page['retrieved_at']})
            issues = raw_coverage['issues']
            if not isinstance(issues, list) or len(issues) > 100:
                raise SourceObservationError('Invalid endpoint issue count')
            safe_issues = []
            for issue in issues:
                code = issue['code']
                if not isinstance(code, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code):
                    raise SourceObservationError('Invalid endpoint issue code')
                safe_issue = {'code': code}
                if 'http_status' in issue:
                    status = issue['http_status']
                    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
                        raise SourceObservationError('Invalid HTTP status')
                    safe_issue['http_status'] = status
                safe_issues.append(safe_issue)
            if complete and safe_issues:
                raise SourceObservationError('Complete endpoint has retrieval issues')
            if complete and not safe_pages:
                raise SourceObservationError('Complete endpoint lacks page provenance')
            if complete and normalization_issues[section]:
                raise SourceObservationError('Complete endpoint has normalization issues')
            fetched[section] = FetchResult(endpoint, data, complete, safe_pages, safe_issues)
            policy = raw_coverage['field_policy']
            count = policy['unretained_field_occurrences']
            if count is not None and (isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 100000):
                raise SourceObservationError('Invalid dropped-field count')
            coverage[section] = {'complete': complete, 'pages': safe_pages, 'issues': safe_issues,
                                 'normalization_issues': normalization_issues[section],
                                 'scope': 'returned_endpoint_pages_not_exhaustive_institutional_history',
                                 'returned_count': raw_coverage['returned_count'],
                                 'unretained_field_occurrences': count}
            if (isinstance(coverage[section]['returned_count'], bool)
                    or not isinstance(coverage[section]['returned_count'], int)
                    or not 0 <= coverage[section]['returned_count'] <= 10000
                    or coverage[section]['returned_count'] < len(rows)
                    or (complete and coverage[section]['returned_count'] != len(rows))):
                raise SourceObservationError('Invalid returned count')
        rebuilt = normalize(pid, fetched, completed_at)
        for section in SECTIONS:
            original_items = ([observation[section]] if section == 'proposition' and observation[section] else
                              observation[section] or [])
            rebuilt_items = ([rebuilt[section]] if section == 'proposition' and rebuilt[section] else
                             rebuilt[section] or [])
            if len(original_items) != len(rebuilt_items):
                raise SourceObservationError('Selected source item rejected')
            for original, checked in zip(original_items, rebuilt_items):
                for name in ('original_fields', 'fingerprint', 'match_key', 'source_date', 'official_identity'):
                    if name in original or name in checked:
                        if original.get(name) != checked.get(name):
                            raise SourceObservationError('Normalized source item mismatch')
                if original.get('evidence_url') != checked['evidence_url'] or original.get('retrieved_at') != completed_at:
                    raise SourceObservationError('Source provenance mismatch')
            if coverage[section]['complete'] and not rebuilt['coverage'][section]['complete']:
                raise SourceObservationError('Untrustworthy complete endpoint')
            rebuilt['coverage'][section]['pages'] = coverage[section]['pages']
            rebuilt['coverage'][section]['issues'] = coverage[section]['issues']
            rebuilt['coverage'][section]['normalization_issues'] = coverage[section]['normalization_issues']
            rebuilt['coverage'][section]['returned_count'] = coverage[section]['returned_count']
            rebuilt['coverage'][section]['field_policy']['unretained_field_occurrences'] = coverage[section]['unretained_field_occurrences']
        # A complete retrieval may be a factual baseline only when normalization
        # did not drop/flag records. Partial/failure attempts retain coverage only.
        if all(rebuilt['coverage'][s]['complete'] for s in SECTIONS):
            source_content_key(rebuilt)
        return rebuilt
    except SourceObservationError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise SourceObservationError('Invalid normalized source observation') from None


def _local_test_target(conn) -> None:
    """No production, Neon or development write path exists in this phase."""
    try:
        params = conn.info.get_parameters()
        host = params.get('host')
        dbname = params.get('dbname')
        if (host not in ('localhost', '127.0.0.1', '::1') or dbname != 'ndq_test'
                or any(params.get(key) for key in ('hostaddr', 'service'))):
            raise SourceObservationError('Source writer requires explicit localhost ndq_test')
        if conn.execute('SELECT current_database()').fetchone()[0] != 'ndq_test':
            raise SourceObservationError('Unexpected database target')
        names = [row[0] for row in conn.execute('SELECT name FROM public.ndq_schema_migrations').fetchall()]
        if '002_source_observations.sql' not in names:
            raise SourceObservationError('Source observation migration is not applied')
    except SourceObservationError:
        raise
    except Exception:
        raise SourceObservationError('Cannot verify local source-writer target') from None


def _stored_snapshot(conn, snapshot_id, pid: int) -> dict:
    row = conn.execute('SELECT first_observed_at, content_hash, coverage FROM ndq.source_snapshots WHERE snapshot_id = %s',
                       (snapshot_id,)).fetchone()
    if not row:
        raise SourceObservationError('Prior source snapshot missing')
    stamp = row[0].isoformat(timespec='seconds').replace('+00:00', 'Z')
    items = conn.execute('''SELECT endpoint_category, ordinal, selected_fields FROM ndq.source_snapshot_items
        WHERE snapshot_id = %s ORDER BY endpoint_category, ordinal''', (snapshot_id,)).fetchall()
    data = {section: [] for section in SECTIONS}
    for section, _, fields in items:
        data[section].append(fields)
    fetched = {section: FetchResult(_endpoint(pid, section),
               data[section][0] if section == 'proposition' and data[section] else
               None if section == 'proposition' else data[section], True)
               for section in SECTIONS}
    result = normalize(pid, fetched, stamp)
    if source_content_key(result) != row[1]:
        raise SourceObservationError('Stored source snapshot checksum mismatch')
    return result


def _items(observation: dict):
    for section in SECTIONS:
        rows = ([observation[section]] if section == 'proposition' and observation[section] else
                observation[section] or [])
        for ordinal, item in enumerate(rows):
            fields = item['original_fields']
            locator = ({'id': fields['id']} if section == 'proposition' else
                       {key: fields.get(key) for key in ('sequencia', 'dataHora', 'siglaOrgao')} if section == 'events' else
                       {'uri': fields.get('uri')} if section == 'authors' else {'id': fields['id']})
            source_date = (fields.get('dataApresentacao') if section == 'proposition' else
                           fields.get('dataHora') if section == 'events' else None)
            semantic = ('presentation_date' if section == 'proposition' else
                        'procedural_source_date' if section == 'events' else 'not_supplied')
            yield section, ordinal, item, locator, source_date, semantic


def _write_validated_observation(conn, checked: dict) -> dict:
    """Shared transaction core. Call only after a target guard and validation."""
    pid = checked['proposition_id']
    identity = ('camara', 'proposicoes', str(pid), PROJECTION_VERSION)
    complete = all(checked['coverage'][section]['complete'] for section in SECTIONS)
    outcome = 'complete' if complete else ('failed' if checked['proposition'] is None and
                all(not checked[section] for section in ('events', 'authors', 'relationships')) else 'incomplete')
    with conn.transaction():
        # Advisory lock orders cooperating attempts for one official entity;
        # unique constraints remain the final protection against other writers.
        conn.execute('SELECT pg_advisory_xact_lock(784331, hashtext(%s))', ('|'.join(identity),))
        previous_row = conn.execute('''SELECT snapshot_id FROM ndq.source_collection_attempts
            WHERE institution = %s AND namespace = %s AND official_id = %s
              AND projection_version = %s AND outcome = 'complete'
            ORDER BY attempt_sequence DESC LIMIT 1''', identity).fetchone()
        previous_id = previous_row[0] if previous_row else None
        snapshot_id, created, candidates = None, False, 0
        if complete:
            content_hash = source_content_key(checked)
            snapshot_id = uuid4()
            inserted = conn.execute('''INSERT INTO ndq.source_snapshots
                (snapshot_id, synthetic_fixture, institution, namespace, official_id, projection_version,
                 content_hash, first_observed_at, coverage)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (institution, namespace, official_id, projection_version, content_hash)
                DO NOTHING RETURNING snapshot_id''',
                (snapshot_id, True, *identity, content_hash, _utc(checked['retrieved_at']), _json(checked['coverage']))).fetchone()
            created = inserted is not None
            if created:
                for section, ordinal, item, locator, source_date, semantic in _items(checked):
                    conn.execute('''INSERT INTO ndq.source_snapshot_items
                        (snapshot_id, endpoint_category, ordinal, match_key, locator, selected_fields,
                         fingerprint, evidence_url, source_date, source_date_semantic, source_identity_uri)
                        VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)''',
                        (snapshot_id, section, ordinal, item.get('match_key'), _json(locator),
                         _json(item['original_fields']), item['fingerprint'], item['evidence_url'],
                         source_date, semantic,
                         item['official_identity']['value'] if section == 'authors' and item.get('official_identity') else None))
            else:
                snapshot_id = conn.execute('''SELECT snapshot_id FROM ndq.source_snapshots
                    WHERE institution = %s AND namespace = %s AND official_id = %s
                      AND projection_version = %s AND content_hash = %s''',
                    (*identity, content_hash)).fetchone()[0]
                # Check that a hash collision or direct SQL corruption did not
                # silently substitute different selected evidence.
                _stored_snapshot(conn, snapshot_id, pid)
            transition = ('initial' if previous_id is None else 'unchanged' if previous_id == snapshot_id
                          else 'changed' if created else 'returned')
        else:
            transition = outcome
        attempt_id = uuid4()
        conn.execute('''INSERT INTO ndq.source_collection_attempts
            (attempt_id, synthetic_fixture, institution, namespace, official_id, projection_version,
             completed_at, outcome, transition_kind, coverage, previous_snapshot_id, snapshot_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)''',
            (attempt_id, True, *identity, _utc(checked['retrieved_at']), outcome, transition,
             _json(checked['coverage']), previous_id, snapshot_id))
        if not complete:
            for section, ordinal, item, locator, source_date, semantic in _items(checked):
                conn.execute('''INSERT INTO ndq.source_attempt_items
                    (attempt_id, endpoint_category, ordinal, match_key, locator, selected_fields,
                     fingerprint, evidence_url, source_date, source_date_semantic, source_identity_uri)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)''',
                    (attempt_id, section, ordinal, item.get('match_key'), _json(locator),
                     _json(item['original_fields']), item['fingerprint'], item['evidence_url'],
                     source_date, semantic,
                     item['official_identity']['value'] if section == 'authors' and item.get('official_identity') else None))
        if complete and previous_id is not None and previous_id != snapshot_id:
            previous = _stored_snapshot(conn, previous_id, pid)
            for finding in compare_snapshots(checked, previous):
                if finding['classification'] == 'unchanged':
                    continue
                if finding['classification'] not in ('new', 'changed', 'conflicting', 'uncertain'):
                    raise SourceObservationError('Unsupported source change classification')
                candidate_key = digest([str(previous_id), str(snapshot_id), finding['section'],
                                        finding['key'], finding['field_path'], finding['classification'], finding['reason']])
                row = conn.execute('''INSERT INTO ndq.source_change_candidates
                    (candidate_id, candidate_key, institution, namespace, official_id, projection_version,
                     first_attempt_id, before_snapshot_id,
                     after_snapshot_id, section, item_key, field_path, classification,
                     reason, before_value, after_value, evidence_url)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                    ON CONFLICT (candidate_key) DO NOTHING RETURNING candidate_id''',
                    (uuid4(), candidate_key, *identity, attempt_id, previous_id, snapshot_id,
                     finding['section'], finding['key'], finding['field_path'],
                     finding['classification'], finding['reason'],
                     _json(finding['before']), _json(finding['after']), finding['evidence_url'])).fetchone()
                candidates += row is not None
    return {'success': True, 'outcome': outcome, 'transition': transition,
            'snapshot_created': created, 'new_change_candidates': candidates,
            'attempt_recorded': True, 'dossier_revision_written': False,
            'editorial_proposal_written': False, 'approval_or_publication_written': False}


def write_local_test_observation(conn, observation: dict, *, allow_local_test: bool = False) -> dict:
    """Sanitized public boundary for the explicitly guarded localhost writer."""
    try:
        if allow_local_test is not True:
            raise SourceObservationError('Source writing is disabled outside explicit local tests')
        checked = validate_source_observation(observation)
        _local_test_target(conn)
        return _write_validated_observation(conn, checked)
    except SourceObservationError:
        raise
    except Exception:
        raise SourceObservationError('Source ingestion failed; transaction rolled back') from None
