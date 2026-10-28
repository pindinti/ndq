"""Review findings, never mutation instructions or automatic editorial approval."""
from __future__ import annotations

from collections import defaultdict

from .baseline import has_identity
from .normalize import event_key


def _groups(items, key):
    groups = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    return groups


def finding(classification, section, key, reason, evidence_url, *, before=None, after=None,
            field_path=None, compared_fields=None):
    return dict(classification=classification, section=section, key=key, reason=reason,
                evidence_url=evidence_url, before=before, after=after, field_path=field_path,
                compared_fields=compared_fields, review_state='pending_review')


def compare_record(observation: dict, baseline: dict) -> list[dict]:
    pid = observation['proposition_id']
    prop_url = f'https://dadosabertos.camara.leg.br/api/v2/proposicoes/{pid}'
    record = baseline.get('record')
    if baseline['status'] != 'matched' or not record:
        kind = 'conflicting' if baseline['status'] == 'conflicting' else 'retrieval_incomplete'
        return [finding(kind, 'baseline', str(pid), 'baseline_' + baseline['status'], prop_url)]
    if not has_identity(record, pid) or not observation['proposition']:
        identity_problem = not has_identity(record, pid) or any(x['code'] in
            ('proposition_identity_conflict', 'proposition_uri_conflict') for x in observation['issues'])
        return [finding('conflicting' if identity_problem else 'retrieval_incomplete',
                        'identity', str(pid), 'identity_or_proposition_unavailable', prop_url)]
    findings = []
    def add(kind, section, key, reason, before=None, after=None):
        url = prop_url + {'events': '/tramitacoes', 'authors': '/autores', 'relationships': '/relacionadas'}.get(section, '')
        findings.append(finding(kind, section, key, reason, url, before=before, after=after))
    def scalar(section, key, before, after):
        if after is None or before is None or after == '' or before == '':
            add('uncertain', section, key, 'missing_or_unmapped_value', before, after)
        elif before != after:
            add('changed', section, key, 'different_documented_value_requires_review', before, after)
        else:
            add('unchanged' if observation['coverage'][section]['complete'] else 'retrieval_incomplete',
                section, key, 'equal_compared_value_only', before, after)

    official = observation['proposition']['original_fields']
    reference = f"{official.get('siglaTipo')} {official.get('numero')}/{official.get('ano')}"
    if reference != record['type_data'].get('legislative_reference'):
        add('conflicting', 'proposition', 'display_reference', 'stable_id_has_different_reference',
            record['type_data'].get('legislative_reference'), reference)
    for predicate, field in (('official_summary', 'ementa'), ('presented_at', 'dataApresentacao')):
        values = [f['value'] for f in record['facts'] if f['predicate'] == predicate]
        if field == 'dataApresentacao' and observation['proposition']['presentation_date']['precision'] in ('unknown', 'month'):
            add('uncertain', 'proposition', field, 'presentation_date_precision_unknown', after=observation['proposition']['presentation_date'])
        else:
            scalar('proposition', field, values[0] if len(values) == 1 else None, official.get(field))
    statuses = {x['status_text'] for x in record['official_status_observations']}
    if len(statuses) > 1:
        add('uncertain', 'proposition', 'status', 'baseline_status_history_requires_temporal_selection', sorted(statuses),
            (official.get('statusProposicao') or {}).get('descricaoSituacao'))
    else:
        scalar('proposition', 'status', next(iter(statuses), None),
               (official.get('statusProposicao') or {}).get('descricaoSituacao'))

    def old_key(event):
        return event_key(pid, event.get('official_sequence'), event.get('occurred_at'), event.get('agency_code'))
    old = _groups(record['events'], old_key)
    current = _groups(observation['events'], lambda e: e['match_key'])
    historical = {old_key(e) for r in baseline.get('history', []) for e in r['events']}
    old_sequences = {e.get('official_sequence') for e in record['events']}
    for key in sorted(set(old) | set(current), key=lambda x: x or ''):
        before, after = old.get(key, []), current.get(key, [])
        if key is None or len(before) > 1 or len(after) > 1:
            add('conflicting' if key else 'uncertain', 'events', key,
                'compound_identity_collision' if key else 'event_identity_incomplete',
                {'count': len(before)}, {'count': len(after)})
        elif not after:
            add('uncertain' if observation['coverage']['events']['complete'] else 'retrieval_incomplete',
                'events', key, 'not_returned_not_a_deletion', before[0])
        elif not before:
            row = after[0]
            sequence = row['original_fields'].get('sequencia')
            uncertain = key in historical or sequence in old_sequences or not row['occurred_at']
            add('uncertain' if uncertain else 'new', 'events', key,
                'possible_historical_or_date_agency_correction' if uncertain else 'new_to_latest_known_baseline',
                after=row)
        else:
            row = after[0]
            scalar('events', key + '/type', before[0].get('event_type'), row['original_fields'].get('descricaoTramitacao'))
            # Curated prose is deliberately excluded from literal source changes.
            # Both representations are retained separately in the report.
    author_groups = _groups(observation['authors'], lambda a: a['match_key'])
    for key, items in sorted(author_groups.items(), key=lambda x: x[0] or ''):
        if key is None or len(items) != 1:
            add('uncertain' if key is None else 'conflicting', 'authors', key, 'author_identity_missing_or_collision')
            continue
        author = items[0]
        add('new', 'authors', key, 'official_author_uri_observed_unlinked_to_legacy_actor',
            after=author)
    if not observation['authors']:
        add('uncertain', 'authors', None, 'no_authors_returned_not_an_absence_claim')
    for key, candidates in _groups(observation['relationships'], lambda x: x['match_key']).items():
        if len(candidates) > 1:
            add('conflicting', 'relationships', key, 'related_identity_collision', after={'count': len(candidates)})
        else:
            add('new', 'relationships', key, 'official_related_candidate_type_unspecified', after=candidates[0])
    # A complete empty endpoint is a valid observation within that endpoint's
    # scope. It is neither a global absence claim nor an editorial alert.
    for section, coverage in observation['coverage'].items():
        if not coverage['complete']:
            add('retrieval_incomplete', section, None, 'endpoint_or_normalization_incomplete', after=coverage)
    return findings


def compare_snapshots(current: dict, previous: dict) -> list[dict]:
    """Literal source comparisons are possible only with a previous normalized observation."""
    if current['proposition_id'] != previous['proposition_id']:
        return [finding('conflicting', 'identity', None, 'previous_proposal_identity_conflict',
                        f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{current['proposition_id']}")]
    findings = []
    for section in ('proposition', 'events', 'authors', 'relationships'):
        now = ([current[section]] if section == 'proposition' and current[section] else current[section] or [])
        old = ([previous[section]] if section == 'proposition' and previous[section] else previous[section] or [])
        if section == 'proposition':
            now = {str(current['proposition_id']): now} if now else {}
            old = {str(previous['proposition_id']): old} if old else {}
        else:
            now, old = _groups(now, lambda x: x.get('match_key')), _groups(old, lambda x: x.get('match_key'))
        complete = current['coverage'][section]['complete'] and previous['coverage'][section]['complete']
        old_sequences = {item['original_fields'].get('sequencia') for item in previous['events']} if section == 'events' else set()
        for key in sorted(set(now) | set(old), key=lambda x: x or ''):
            a, b = old.get(key, []), now.get(key, [])
            url = (b or a)[0]['evidence_url']
            if key is None or len(a) > 1 or len(b) > 1:
                kind, reason = 'conflicting', 'compound_identity_collision_or_missing_key'
            elif not b:
                kind, reason = ('uncertain' if complete else 'retrieval_incomplete'), 'not_returned_not_a_deletion'
            elif not a:
                possible_rematch = section == 'events' and b[0]['original_fields'].get('sequencia') in old_sequences
                kind = 'uncertain' if possible_rematch or not complete else 'new'
                reason = 'possible_event_date_or_agency_correction' if possible_rematch else 'new_to_previous_source_observation'
            elif a[0]['fingerprint'] != b[0]['fingerprint']:
                for path, before, after, missing in _field_differences(a[0]['original_fields'], b[0]['original_fields']):
                    findings.append(finding('uncertain' if missing else 'changed', section, key,
                                            'literal_field_missing_or_cleared' if missing else 'selected_official_field_changed',
                                            url, before=before, after=after, field_path=path))
                continue
            else:
                kind, reason = ('unchanged' if complete else 'retrieval_incomplete'), 'equal_selected_literal_fields_only'
            findings.append(finding(kind, section, key, reason, url,
                                    before=a[0]['original_fields'] if len(a) == 1 else None,
                                    after=b[0]['original_fields'] if len(b) == 1 else None,
                                    compared_fields=current['coverage'][section].get('field_policy', {}).get('compared_with_prior_source')
                                    if a and b and kind == 'unchanged' else None))
    return findings


def _field_differences(before, after, prefix=''):
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            path = f'{prefix}.{key}' if prefix else key
            if key not in before or key not in after:
                yield path, before.get(key), after.get(key), True
            else:
                yield from _field_differences(before[key], after[key], path)
    elif before != after:
        yield prefix, before, after, before in (None, '') or after in (None, '')
