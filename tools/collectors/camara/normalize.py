"""Field-allowlisted observations, with source dates separate from retrieval UTC."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

from tools.collectors.http import BASE, FetchResult

EVENT_FIELDS = ('dataHora sequencia siglaOrgao uriOrgao uriUltimoRelator regime '
                'descricaoTramitacao codTipoTramitacao descricaoSituacao codSituacao despacho url ambito apreciacao').split()
PROP_FIELDS = 'id uri siglaTipo codTipo numero ano descricaoTipo ementa dataApresentacao uriOrgaoNumerador urnFinal'.split()
STATUS_FIELDS = EVENT_FIELDS
AUTHOR_FIELDS = 'uri nome codTipo tipo ordemAssinatura proponente'.split()
RELATED_FIELDS = 'id uri siglaTipo codTipo numero ano ementa'.split()
# This is a versioned projection, not a copy of the API response. New API fields
# are dropped by policy until their semantics and safe size have been reviewed.
RETENTION = {'proposition': tuple(PROP_FIELDS), 'statusProposicao': tuple(STATUS_FIELDS),
             'events': tuple(EVENT_FIELDS), 'authors': tuple(AUTHOR_FIELDS),
             'relationships': tuple(RELATED_FIELDS)}
COMPARISON_FIELDS = {key: value for key, value in RETENTION.items()}
PROJECTION_VERSION = 'camara-selected-fields-v1'
INTEGER_FIELDS = {'id', 'numero', 'ano', 'sequencia', 'ordemAssinatura', 'proponente'}
CODE_FIELDS = {'codTipo', 'codTipoTramitacao', 'codSituacao'}
URL_FIELDS = {'uri', 'uriOrgao', 'uriUltimoRelator', 'uriOrgaoNumerador', 'url'}


class NormalizationError(ValueError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def official_url(value: str) -> str:
    try:
        u = urlsplit(value)
        if (u.scheme != 'https' or not u.hostname or u.username or u.password
                or u.port not in (None, 443) or u.fragment
                or not (u.hostname == 'camara.leg.br' or u.hostname.endswith('.camara.leg.br'))
                or any(re.search('password|token|secret|credential', key, re.I) for key in parse_qs(u.query))
                or len(value) > 2000 or any(ch.isspace() for ch in value)):
            raise NormalizationError('invalid_official_url')
    except (ValueError, TypeError):
        raise NormalizationError('invalid_official_url') from None
    return value


def fields(row: dict, names: list[str]) -> dict:
    if not isinstance(row, dict):
        raise NormalizationError('expected_object')
    out = {}
    for key in names:
        if key not in row:
            continue
        value = row[key]
        if value is not None:
            if key in INTEGER_FIELDS:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise NormalizationError('invalid_integer_field')
            elif key in CODE_FIELDS:
                if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
                    raise NormalizationError('invalid_code_field')
            elif not isinstance(value, str) or len(value) > 16000:
                raise NormalizationError('invalid_text_field')
            if key in URL_FIELDS and value:
                official_url(value)
        out[key] = value
    return out


def temporal(value, role: str) -> dict:
    result = {'value': value, 'precision': 'unknown', 'timezone_status': 'unknown', 'semantic': role}
    if value is None:
        return result
    if not isinstance(value, str):
        raise NormalizationError('invalid_date_type')
    try:
        if re.fullmatch(r'\d{4}-\d{2}', value):
            date.fromisoformat(value + '-01')
            result.update(precision='month', timezone_status='not_applicable')
        elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            date.fromisoformat(value)
            result.update(precision='day', timezone_status='date_only')
        elif re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?', value):
            datetime.fromisoformat(value.replace('Z', '+00:00'))
            time_part = re.sub(r'(Z|[+-]\d{2}:\d{2})$', '', value.split('T')[1])
            precision = 'fractional_second' if '.' in time_part else 'second' if time_part.count(':') == 2 else 'minute'
            result.update(precision=precision, timezone_status='offset_provided' if re.search(r'(Z|[+-]\d{2}:\d{2})$', value)
                          else 'not_provided_by_source')
    except ValueError:
        pass
    return result


def event_key(proposition_id: int, sequence, source_date, agency) -> str | None:
    if (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1
            or not isinstance(source_date, str) or temporal(source_date, 'procedural_date')['precision'] not in
            ('day', 'minute', 'second', 'fractional_second')
            or not isinstance(agency, str) or not agency.strip()):
        return None
    # A local matching candidate, not an official event ID. Descriptions excluded.
    return 'camara-compound-v1:' + digest([proposition_id, sequence, source_date, agency])


def _item(original: dict, endpoint: str, observed_at: str) -> dict:
    return {'original_fields': original, 'evidence_url': endpoint,
            'retrieved_at': observed_at, 'retrieval_time_scope': 'collection_completed_at',
            'fingerprint': digest(original)}


def normalize(proposition_id: int, fetched: dict[str, FetchResult], observed_at: str) -> dict:
    if isinstance(proposition_id, bool) or not isinstance(proposition_id, int) or proposition_id < 1:
        raise NormalizationError('invalid_proposition_id')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', observed_at):
        raise NormalizationError('retrieval_timestamp_must_be_utc')
    datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
    result = {'proposition_id': proposition_id, 'retrieved_at': observed_at,
              'retrieval_time_scope': 'collection_completed_at',
              'proposition': None, 'events': [], 'authors': [], 'relationships': [],
              'coverage': {name: value.coverage() for name, value in fetched.items()},
              'projection_version': PROJECTION_VERSION, 'issues': []}
    def issue(section, code):
        result['issues'].append({'section': section, 'code': code})
        result['coverage'][section]['complete'] = False

    prop = fetched['proposition']
    if prop.data is not None:
        try:
            original = fields(prop.data, PROP_FIELDS)
            if original.get('id') != proposition_id:
                raise NormalizationError('proposition_identity_conflict')
            if original.get('uri') and original['uri'] != f'{BASE}/proposicoes/{proposition_id}':
                raise NormalizationError('proposition_uri_conflict')
            if not original.get('siglaTipo') or not original.get('numero') or not original.get('ano'):
                raise NormalizationError('proposition_reference_missing')
            status = prop.data.get('statusProposicao')
            if status is not None:
                original['statusProposicao'] = fields(status, STATUS_FIELDS)
            item = _item(original, prop.endpoint, observed_at)
            item['presentation_date'] = temporal(original.get('dataApresentacao'), 'presentation_date')
            item['status_date'] = temporal((original.get('statusProposicao') or {}).get('dataHora'), 'status_record_date')
            if original.get('dataApresentacao') is not None and item['presentation_date']['precision'] == 'unknown':
                issue('proposition', 'presentation_date_precision_unknown')
            if ((original.get('statusProposicao') or {}).get('dataHora') is not None
                    and item['status_date']['precision'] == 'unknown'):
                issue('proposition', 'status_date_precision_unknown')
            result['proposition'] = item
        except NormalizationError as exc:
            issue('proposition', str(exc))
    for name, allowed in (('events', EVENT_FIELDS), ('authors', AUTHOR_FIELDS), ('relationships', RELATED_FIELDS)):
        response = fetched[name]
        for row in response.data or []:
            try:
                original = fields(row, allowed)
                item = _item(original, response.endpoint, observed_at)
                if name == 'events':
                    source_date = temporal(original.get('dataHora'), 'procedural_source_date')
                    item.update(source_date=source_date, procedural_date=source_date,
                                published_at=None, scheduled_for=None,
                                occurred_at=None, event_state='date_unknown',
                                match_key=event_key(proposition_id, original.get('sequencia'), original.get('dataHora'), original.get('siglaOrgao')))
                    if source_date['precision'] in ('day', 'minute', 'second', 'fractional_second'):
                        future = original['dataHora'][:10] > observed_at[:10]
                        if source_date['timezone_status'] == 'offset_provided':
                            future = datetime.fromisoformat(original['dataHora'].replace('Z', '+00:00')) > datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
                        if future:
                            item['event_state'] = 'future_source_date_unconfirmed'
                        else:
                            # dataHora is a source procedural timestamp. Even 00:00
                            # does not establish an exact instant of occurrence.
                            item['event_state'] = 'source_reported_procedural_date_not_exact_occurrence'
                    if source_date['precision'] not in ('day', 'minute', 'second', 'fractional_second') or item['match_key'] is None:
                        issue(name, 'event_date_or_identity_uncertain')
                    if not original.get('descricaoTramitacao') or not original.get('despacho'):
                        issue(name, 'event_description_missing')
                elif name == 'authors':
                    if not original.get('nome'):
                        raise NormalizationError('author_name_missing')
                    item['match_key'] = original.get('uri')
                    item['role'] = 'author_as_returned_not_rapporteur'
                    item['official_identity'] = ({'institution': 'camara', 'namespace': 'author_uri',
                                                  'value': original['uri'], 'source_url': response.endpoint,
                                                  'link_to_legacy_actor': 'unresolved'}
                                                 if original.get('uri') else None)
                else:
                    if not original.get('id') or original['id'] == proposition_id:
                        raise NormalizationError('related_identity_invalid')
                    if original.get('uri') != f"{BASE}/proposicoes/{original['id']}":
                        raise NormalizationError('related_uri_identity_conflict')
                    item.update(match_key=str(original['id']), relationship_type='returned_as_related_unspecified',
                                origin='official_endpoint', review_state='pending_review', legal_effect=None)
                result[name].append(item)
            except NormalizationError as exc:
                issue(name, str(exc))
        result[name].sort(key=lambda x: (x.get('match_key') or '', x['fingerprint']))
    # Coverage describes the selected projection explicitly. No unknown key names,
    # unknown values, raw envelopes, documents, or credential-like strings survive.
    for name, allowed in (('proposition', PROP_FIELDS + ['statusProposicao']), ('events', EVENT_FIELDS),
                          ('authors', AUTHOR_FIELDS), ('relationships', RELATED_FIELDS)):
        response = fetched[name]
        rows = ([response.data] if name == 'proposition' and response.data is not None else response.data or [])
        omitted_count = sum(len(set(row) - set(allowed)) for row in rows)
        status = response.data.get('statusProposicao') if name == 'proposition' and isinstance(response.data, dict) else None
        if isinstance(status, dict):
            omitted_count += len(set(status) - set(STATUS_FIELDS))
        result['coverage'][name]['field_policy'] = {
            'projection_version': PROJECTION_VERSION,
            'retained': list(RETENTION[name]), 'compared_with_prior_source': list(COMPARISON_FIELDS[name]),
            'unretained_field_occurrences': omitted_count,
            'unretained_policy': 'drop_unreviewed_fields_no_raw_payload',
            'comparison_scope': 'selected_fields_only_not_complete_change_detection'}
        result['coverage'][name]['returned_count'] = len(rows)
        if name == 'proposition':
            result['coverage'][name]['field_policy']['nested_status'] = {
                'retained': list(STATUS_FIELDS), 'compared_with_prior_source': list(COMPARISON_FIELDS['statusProposicao'])}
    return result
