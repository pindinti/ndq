"""Small SQL repository. Public-site generation deliberately does not use it."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlsplit

from .contract import Pilot, SECTIONS, canonical, digest, item_key, summary


class IdentityConflict(ValueError):
    pass


def _json(value):
    # Explicit casts keep JSONB handling independent of psycopg adapters.
    return canonical(value)


def _insert(conn, table: str, values: dict):
    # Only internal constants form identifiers, never record contents.
    from psycopg import sql
    columns = list(values)
    json_fields = {'metadata', 'data', 'value', 'previous_value', 'new_value', 'assessment', 'report'}
    query = sql.SQL('INSERT INTO ndq.{} ({}) VALUES ({})').format(
        sql.Identifier(table), sql.SQL(', ').join(map(sql.Identifier, columns)),
        sql.SQL(', ').join(sql.SQL('%s::jsonb' if c in json_fields and not
                                  (table in ('official_identities', 'identity_observations') and c == 'value') else '%s')
                           for c in columns))
    conn.execute(query, tuple(values.values()))


def _observation(conn, rev, section, item, position):
    category = SECTIONS[section][0]
    refs = item.get('basis_source_ids' if category == 'change' else 'source_ids')
    key = item_key(section, item)
    _insert(conn, 'observations', dict(revision_id=rev, observation_key=key, category=category,
            position=position, primary_source_id=refs[0], observed_at=item.get('observed_at'), data=_json(item)))
    for order, ref in enumerate(refs):
        _insert(conn, 'observation_evidence', dict(revision_id=rev, observation_key=key,
                source_id=ref, citation_order=order))
    return dict(revision_id=rev, observation_key=key)


def _identity(conn, dossier_id, base, item):
    ident = (item['institution'], item['namespace'], str(item['value']))
    owner = conn.execute('''SELECT dossier_id FROM ndq.official_identities
        WHERE institution = %s AND namespace = %s AND value = %s''', ident).fetchone()
    if owner and owner[0] != dossier_id:
        raise IdentityConflict('Official identity belongs to another dossier; automatic merge refused')
    if not owner:
        _insert(conn, 'official_identities', dict(institution=ident[0], namespace=ident[1], value=ident[2],
                dossier_id=dossier_id, first_revision_id=base['revision_id'], first_observation_key=base['observation_key']))
    _insert(conn, 'identity_observations', dict(**base, institution=ident[0], namespace=ident[1], value=ident[2],
            reference_url=item['reference_url'], verified_at=item['verified_at']))


def _write_revision(conn, pilot, run_id, previous):
    r = pilot.record
    dossier_id = r['dossier_id']
    rev = uuid4()
    number = previous[1] + 1 if previous else 1
    metadata = {k: v for k, v in r.items() if k not in
                {*SECTIONS, 'source_references', 'editorial_assessment', 'publication_status'}}
    _insert(conn, 'dossier_revisions', dict(revision_id=rev, dossier_id=dossier_id, revision_number=number,
            previous_revision_id=previous[0] if previous else None, run_id=run_id, slug=r['slug'],
            content_hash=pilot.content_hash, source_observed_at=r['last_observed_at'],
            present_sections=[s for s in SECTIONS if s in r], metadata=_json(metadata)))
    for pos, src in enumerate(r['source_references']):
        _insert(conn, 'document_references', dict(revision_id=rev, source_id=src['source_id'], position=pos,
                host=urlsplit(src['url']).hostname, url=src['url'], source_type=src['source_type'],
                observed_at=src['observed_at'], access_state=src['access_state'], metadata=_json(src)))
    for section in SECTIONS:
        for pos, item in enumerate(r.get(section, [])):
            base = _observation(conn, rev, section, item, pos)
            if section == 'official_identities':
                _identity(conn, dossier_id, base, item)
            elif section == 'facts':
                _insert(conn, 'facts', dict(**base, predicate=item['predicate'], value=_json(item['value']),
                        verification_status=item['verification_status'], effective_at=item.get('effective_at')))
            elif section == 'events':
                _insert(conn, 'events', dict(**base, event_type=item['event_type'], event_state='occurred',
                        occurred_at=item['occurred_at'], published_at=item.get('published_at'),
                        observed_at=item['observed_at'], timezone_status=item['occurrence_timezone_status']))
            elif section == 'contextual_milestones':
                _insert(conn, 'contextual_milestones', dict(**base, context_kind=item['context_kind'],
                        date_precision=item['date_precision'], period=item['period'], scheduled_for=item.get('scheduled_for')))
            elif section == 'official_status_observations':
                _insert(conn, 'status_observations', dict(**base, status_text=item['status_text'], institution=item['institution']))
            elif section == 'actor_roles':
                # Scope names to a dossier; matching names never merge people globally.
                actor_id = 'pilot-name:' + digest([dossier_id, item['actor_name']])
                conn.execute('''INSERT INTO ndq.actors (actor_id, dossier_id, name) VALUES (%s, %s, %s)
                    ON CONFLICT (actor_id) DO NOTHING''', (actor_id, dossier_id, item['actor_name']))
                _insert(conn, 'actor_roles', dict(**base, actor_id=actor_id, role_type=item['role_type'], role_date=item.get('role_date')))
            elif section == 'change_history':
                _insert(conn, 'documented_changes', dict(**base, attribute_path=item['attribute_path'],
                        previous_value=_json(item['previous_observed_value']), new_value=_json(item['new_observed_value']),
                        detected_at=item['detected_at']))
    assessment = r['editorial_assessment']
    _insert(conn, 'editorial_reviews', dict(revision_id=rev, review_state='pending_review',
            origin_review_status=assessment['review_status'], reviewer=None, assessment=_json(assessment)))
    for fid in assessment['basis_fact_ids']:
        _insert(conn, 'editorial_basis', dict(revision_id=rev, fact_key='facts/' + fid))
    _insert(conn, 'publication_states', dict(revision_id=rev, state='private_preview_only'))
    return rev, number


def import_pilots(conn, pilots: list[Pilot]) -> dict:
    """Atomic batch; latest identical content is a no-op, A->B->A is three revisions.

    Accepts only the validated adapter output, rechecking hashes to prevent mutation
    between offline validation and import. Conflicts roll back the entire batch.
    """
    from .contract import validate, LEGACY
    import json
    for p in pilots:
        validate(p.record, json.loads((LEGACY / (p.record['slug'] + '.json')).read_text('utf-8')))
        if p.content_hash != digest(p.record):
            raise ValueError('Pilot changed after validation')
    if len({p.record['dossier_id'] for p in pilots}) != len(pilots):
        raise IdentityConflict('Duplicate dossier in batch')
    started = datetime.now(timezone.utc)
    report = summary(pilots)
    report.update(mode='database_import', committed=False, imported=[], unchanged=[])
    with conn.transaction():
        # Serializes cooperating importers, including concurrent first imports.
        conn.execute('SELECT pg_advisory_xact_lock(784331, 2)')
        pending = []
        for pilot in pilots:
            r = pilot.record
            previous = conn.execute('''SELECT revision_id, revision_number, content_hash FROM ndq.latest_revisions
                WHERE dossier_id = %s''', (r['dossier_id'],)).fetchone()
            if previous and previous[2] == pilot.content_hash:
                report['unchanged'].append(r['dossier_id'])
            else:
                pending.append((pilot, previous))
        if not pending:
            report.update(committed=True, run_id=None)
            return report
        run_id = uuid4()
        hosts = sorted({urlsplit(s['url']).hostname for p, _ in pending for s in p.record['source_references']})
        for host in hosts:
            conn.execute('INSERT INTO ndq.source_systems (host) VALUES (%s) ON CONFLICT DO NOTHING', (host,))
            _insert(conn, 'run_sources', dict(run_id=run_id, host=host))
        for pilot, previous in pending:
            r = pilot.record
            before = read_record(conn, previous[0]) if previous else {}
            changed_sections = sorted(k for k in set(before) | set(r) if k not in before or k not in r or before[k] != r[k])
            existing = conn.execute('SELECT kind FROM ndq.dossiers WHERE dossier_id = %s', (r['dossier_id'],)).fetchone()
            if existing and existing[0] != r['kind']:
                raise IdentityConflict('Changing a stable dossier kind requires a reviewed migration')
            conn.execute('INSERT INTO ndq.dossiers (dossier_id, kind) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                         (r['dossier_id'], r['kind']))
            route = conn.execute('SELECT dossier_id FROM ndq.dossier_slugs WHERE slug = %s', (r['slug'],)).fetchone()
            if route and route[0] != r['dossier_id']:
                raise IdentityConflict('Slug already belongs to another dossier')
            conn.execute('INSERT INTO ndq.dossier_slugs (slug, dossier_id) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                         (r['slug'], r['dossier_id']))
            rev, number = _write_revision(conn, pilot, run_id, previous)
            report['imported'].append(dict(dossier_id=r['dossier_id'], revision_id=str(rev), revision_number=number,
                    previous_hash=previous[2] if previous else None, sha256=pilot.content_hash, changed_sections=changed_sections))
        report.update(run_id=str(run_id))
        # Deferred run FKs permit an accurate completion timestamp and complete
        # report, inserted once after all revisions succeed in this transaction.
        _insert(conn, 'collection_runs', dict(run_id=run_id, origin='fixture_import', started_at=started,
                completed_at=datetime.now(timezone.utc), report=_json({'adapter': 'v0.3-pilots',
                'input_counts': report['counts'], 'imported': report['imported'], 'unchanged': report['unchanged']})))
    report['committed'] = True
    return report


def read_record(conn, revision_id) -> dict:
    """Reconstruct the exact structured record for inspection/history, never publish."""
    row = conn.execute('SELECT metadata, content_hash, present_sections FROM ndq.dossier_revisions WHERE revision_id = %s',
                       (revision_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown revision')
    r = row[0]
    r['source_references'] = [x[0] for x in conn.execute('''SELECT metadata FROM ndq.document_references
        WHERE revision_id = %s ORDER BY position''', (revision_id,)).fetchall()]
    for section, (category, _) in SECTIONS.items():
        if section not in row[2]:
            continue
        r[section] = [x[0] for x in conn.execute('''SELECT data FROM ndq.observations
            WHERE revision_id = %s AND category = %s ORDER BY position''', (revision_id, category)).fetchall()]
    r['editorial_assessment'] = conn.execute('SELECT assessment FROM ndq.editorial_reviews WHERE revision_id = %s',
                                            (revision_id,)).fetchone()[0]
    r['publication_status'] = conn.execute('SELECT state FROM ndq.publication_states WHERE revision_id = %s',
                                          (revision_id,)).fetchone()[0]
    if digest(r) != row[1]:
        raise ValueError('Stored revision failed structured content checksum')
    return r


def schedule_check(conn, *, revision_id, basis_key: str, due_at: datetime, reason: str, idempotency_key: str):
    """Explicit operational time only; no implicit D+1 or source-zone assumption."""
    if due_at.tzinfo is None or due_at.utcoffset() is None:
        raise ValueError('Scheduled check requires an explicitly timezone-aware datetime')
    if not reason.strip() or not idempotency_key.strip():
        raise ValueError('Reason and idempotency key required')
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(784331, 3)')
        existing = conn.execute('''SELECT check_id, revision_id, basis_observation_key, due_at, reason
            FROM ndq.scheduled_checks WHERE idempotency_key = %s''', (idempotency_key,)).fetchone()
        if existing:
            if (str(existing[1]), existing[2], existing[3], existing[4]) != (str(revision_id), basis_key, due_at, reason):
                raise ValueError('Idempotency key was reused for a different scheduled check')
            return existing[0]
        check_id = uuid4()
        _insert(conn, 'scheduled_checks', dict(check_id=check_id, revision_id=revision_id,
                basis_observation_key=basis_key, due_at=due_at, reason=reason, idempotency_key=idempotency_key))
        return check_id
