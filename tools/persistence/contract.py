"""Closed adapter for the provisional v0.3 pilot contract; no source fetching."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from tools.validate_dossiers import LEGACY, RECORDS, validate_record

SECTIONS = {
    'official_identities': ('identity', None),
    'facts': ('fact', 'fact_id'),
    'events': ('event', 'event_id'),
    'contextual_milestones': ('milestone', 'milestone_id'),
    'official_status_observations': ('status', None),
    'actor_roles': ('actor_role', None),
    'change_history': ('change', 'change_id'),
}
FIELDS = {
    'official_identities': 'institution namespace value display_ref reference_url verified_at source_ids',
    'source_references': 'source_id label url source_type locator observed_at access_state source_version display_label display_description display_note',
    'facts': 'fact_id predicate value source_ids source_locator observed_at effective_at verification_status',
    'events': 'event_id event_id_kind event_type description occurred_at occurrence_timezone_status published_at observed_at source_ids official_sequence agency_code display_title display_badge',
    'contextual_milestones': 'milestone_id period date_precision display_date display_badge title description context_kind source_ids observed_at scheduled_for',
    'official_status_observations': 'status_text institution observed_at source_ids stage_is_not_legal_effect',
    'actor_roles': 'actor_name role_type role_date affiliation_at_date source_ids',
    'change_history': 'change_id attribute_path previous_observed_value new_observed_value delta_brl_cents detected_at basis_source_ids change_classification review_status does_not_establish_contract_or_delivery',
    'presentation': 'group type eyebrow title description status relevance sort',
    'scope': 'sphere uf',
    'quality_states': 'transport_status parse_status identity_status coverage_status fact_verification_status editorial_status',
    'editorial_assessment': 'relevance basis_fact_ids suggested_by reviewer review_status impact_claims',
    'notice_versions': 'notice_type document_date maximum_value_brl_cents source_ids',
    'item_requirements': 'name details description source_ids',
}
TYPE_FIELDS = {
    'legislative_proposal': 'legislative_reference stage_not_enactment law_in_force',
    'regulatory_agenda_theme': 'agenda_cycle theme_id macrotheme agenda_status_source last_read_spreadsheet_version proposals related_act_mentions normative_effect public_consultation_open plant_based_implies_certified_vegan',
    'public_procurement': 'procurement_object notice_versions item_requirements procurement_lifecycle_events contract_signed goods_delivered',
}
ENVELOPE = set(('schema_version dossier_id slug kind institution scope created_at last_observed_at '
                'publication_status presentation theme official_identities source_references facts events '
                'official_status_observations actor_roles dossier_relations deadlines coverage_evidence change_history '
                'quality_states editorial_assessment provenance_note type_data contextual_milestones editorial_template_version').split())


class ContractError(ValueError):
    pass


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed.split()):
        raise ContractError(f'{label}: unsupported fields or expected object')


def temporal(value, *, month=False):
    if not isinstance(value, str):
        raise ContractError('Expected source date/time string')
    if month and re.fullmatch(r'\d{4}-\d{2}', value):
        date.fromisoformat(value + '-01')
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        date.fromisoformat(value)
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d{1,6})?)?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)?', value):
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise ContractError(f'Unsupported source temporal precision: {value!r}')


def _bounded(value):
    if isinstance(value, dict):
        for v in value.values():
            _bounded(v)
    elif isinstance(value, list):
        for v in value:
            _bounded(v)
    elif isinstance(value, str):
        if len(value) > 16000 or re.search(r'<!doctype|<html[\s>]|%PDF-', value, re.I):
            raise ContractError('Only bounded structured metadata is supported; no document bodies')


def validate(record: dict, legacy: dict) -> None:
    """Strict validation before any database connection or write is attempted."""
    try:
        if not isinstance(record, dict) or set(record) - ENVELOPE or ENVELOPE - set(record) - {'contextual_milestones'}:
            raise ContractError('Unsupported/missing envelope fields; adapter requires v0.3 pilot records')
        _bounded(record)
        for name, allowed in FIELDS.items():
            if name not in record:
                continue
            items = record[name]
            if name in SECTIONS or name == 'source_references':
                if not isinstance(items, list):
                    raise ContractError(f'{name}: expected list')
                for item in items:
                    fields(item, allowed, name)
            else:
                fields(items, allowed, name)
        # Their populated formats have not been established by the pilot contract.
        for name in ('dossier_relations', 'deadlines', 'coverage_evidence'):
            if record[name] != []:
                raise ContractError(f'{name}: populated records are unsupported; define a reviewed adapter first')
        kind = record['kind']
        fields(record['type_data'], TYPE_FIELDS[kind], 'type_data')
        if set(record['type_data']) != set(TYPE_FIELDS[kind].split()):
            raise ContractError('Missing provisional type_data fields')
        for name in ('proposals', 'related_act_mentions', 'procurement_lifecycle_events'):
            if record['type_data'].get(name, []) != []:
                raise ContractError(f'type_data.{name}: populated records unsupported')
        for name in ('notice_versions', 'item_requirements'):
            for item in record['type_data'].get(name, []):
                fields(item, FIELDS[name], name)
        errors = validate_record(record, legacy, slug=record['slug'])
        if errors:
            raise ContractError('; '.join(errors))
        if not re.fullmatch(r'ndq:[^\s]+', record['dossier_id']):
            raise ContractError('Invalid stable dossier identity')
        source_ids = {s['source_id'] for s in record['source_references']}
        if not isinstance(record['theme'], list) or any(not isinstance(t, str) for t in record['theme']):
            raise ContractError('theme must be a list of strings')
        for ident in record['official_identities']:
            if any(not isinstance(ident.get(k), str) or not ident[k].strip() for k in ('institution', 'namespace', 'value')):
                raise ContractError('Official identity components must be nonempty strings')
        for src in record['source_references']:
            if src['source_type'] not in ('institutional_html', 'institutional_json', 'institutional_xlsx', 'official_pdf', 'institutional_document_link'):
                raise ContractError('Unsupported document reference type')
            if not isinstance(src.get('label'), str) or not src['label'].strip():
                raise ContractError('Document reference label is required')
        for section in SECTIONS:
            keys = set()
            for item in record.get(section, []):
                key = item_key(section, item)
                if key in keys:
                    raise ContractError(f'{section}: duplicate observation')
                keys.add(key)
                refs = item.get('basis_source_ids' if section == 'change_history' else 'source_ids')
                if not isinstance(refs, list) or not refs or len(set(refs)) != len(refs) or set(refs) - source_ids:
                    raise ContractError(f'{section}: missing, duplicate or unknown provenance')
                for field in ('observed_at', 'occurred_at', 'published_at', 'effective_at', 'verified_at', 'role_date', 'detected_at', 'scheduled_for'):
                    if item.get(field) is not None:
                        temporal(item[field])
                required_text = {
                    'facts': ('predicate', 'verification_status'),
                    'events': ('event_type', 'description'),
                    'official_status_observations': ('status_text', 'institution'),
                    'actor_roles': ('actor_name', 'role_type'),
                    'change_history': ('attribute_path',),
                }.get(section, ())
                if any(not isinstance(item.get(k), str) or not item[k].strip() for k in required_text):
                    raise ContractError(f'{section}: required text missing or invalid')
                required_date = {'facts': 'observed_at', 'official_status_observations': 'observed_at',
                                 'change_history': 'detected_at'}.get(section)
                if required_date:
                    temporal(item.get(required_date))
        temporal(record['last_observed_at'])
        if record['created_at'] is not None:
            temporal(record['created_at'])
        for src in record['source_references']:
            temporal(src['observed_at'])
        for ev in record['events']:
            if not ev.get('occurred_at') or not ev.get('observed_at') or not ev.get('event_type') or not ev.get('description'):
                raise ContractError('Events require occurrence, observation, type and description')
            if kind == 'public_procurement' and ev['event_type'] not in ('notice_original', 'notice_rectification'):
                raise ContractError('Procurement pilot supports documentary events only, not scheduled sessions or execution claims')
            occurrence, observed = ev['occurred_at'], ev['observed_at']
            if date.fromisoformat(occurrence[:10]) > date.fromisoformat(observed[:10]):
                raise ContractError('Future/scheduled event cannot be imported as occurred')
            if len(occurrence) == 10:
                zone = 'date_only'
            elif re.search(r'(Z|[+-]\d{2}:\d{2})$', occurrence):
                zone = 'offset_provided'
            else:
                zone = 'not_provided_by_source'
            if ev.get('occurrence_timezone_status') != zone:
                raise ContractError('Occurrence precision/timezone status mismatch')
        for f in record['facts']:
            if not f.get('predicate') or 'value' not in f or not f.get('verification_status'):
                raise ContractError('Incomplete fact')
            values = f['value'] if isinstance(f['value'], list) else [f['value']]
            if any(isinstance(v, (dict, list)) for v in values):
                raise ContractError('Fact values must be scalars or scalar lists, not raw response objects')
        for role in record['actor_roles']:
            if not role.get('actor_name') or not role.get('role_type'):
                raise ContractError('Incomplete actor role')
        # Preserve the prior assessment, but never turn it into fresh approval.
        if record['editorial_assessment']['reviewer'] is not None:
            raise ContractError('Pilot reviewer must remain unknown; use future review workflow')
        canonical(record)  # also rejects NaN/infinities
    except ContractError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ContractError(f'Invalid or unsupported pilot record ({type(exc).__name__})') from None


def item_key(section: str, item: dict) -> str:
    _, id_field = SECTIONS[section]
    if section == 'official_identities':
        local = digest([item['institution'], item['namespace'], str(item['value'])])
    elif id_field:
        local = item[id_field]
        if not isinstance(local, str) or not local.strip():
            raise ContractError(f'{section}: missing local identity')
    else:
        # No official event/actor/status id is fabricated for records lacking one.
        local = 'local-content:' + digest(item)
    return section + '/' + local


@dataclass(frozen=True)
class Pilot:
    record: dict
    content_hash: str


def load_pilots(records_dir: Path = RECORDS, legacy_dir: Path = LEGACY) -> list[Pilot]:
    files = sorted(records_dir.glob('*.json'))
    if not files:
        raise ContractError('No pilot JSON records found')
    pilots, dossiers, identities, slugs = [], set(), set(), set()
    for path in files:
        try:
            record = json.loads(path.read_text('utf-8'))
            legacy = json.loads((legacy_dir / path.name).read_text('utf-8'))
        except (OSError, ValueError):
            raise ContractError(f'{path.name}: cannot read record/legacy JSON') from None
        validate(record, legacy)
        if path.stem != record['slug']:
            raise ContractError(f'{path.name}: filename/slug mismatch')
        if record['dossier_id'] in dossiers or record['slug'] in slugs:
            raise ContractError('Duplicate dossier or slug in import batch')
        dossiers.add(record['dossier_id'])
        slugs.add(record['slug'])
        for ident in record['official_identities']:
            key = (ident['institution'], ident['namespace'], str(ident['value']))
            if key in identities:
                raise ContractError('Official identity reused across dossiers; no automatic merging')
            identities.add(key)
        pilots.append(Pilot(record, digest(record)))
    return pilots


def summary(pilots: list[Pilot]) -> dict:
    return {'mode': 'offline_validation', 'dossiers': len(pilots),
            'counts': {name: sum(len(p.record.get(name, [])) for p in pilots)
                       for name in ('source_references', *SECTIONS)},
            'records': [{'dossier_id': p.record['dossier_id'], 'sha256': p.content_hash}
                        for p in pilots],
            'unknown_semantics': 'Empty lists mean not populated, never proof of absence',
            'review_state': 'pending_review', 'publication_state': 'private_preview_only'}
