#!/usr/bin/env python3
"""Validate structured NDQ dossier pilot records and their v0.1 editorial HTML bridges.

No network, no publication, no normalization/inference beyond provided sources.
Uses Python standard library only. `[]` means not loaded, NEVER proves absence.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / 'observatorio' / 'conteudo' / 'registros'
LEGACY = ROOT / 'observatorio' / 'conteudo' / 'dossies'
SLUG = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
KINDS = {
    'legislative_proposal': ('legislacao', 'Projeto de lei'),
    'regulatory_agenda_theme': ('regulacao', 'Agenda regulatória'),
    'public_procurement': ('compras', 'Compra pública'),
}
SOURCE_LIST_FIELDS = (
    ('official_identities', 'source_ids'),
    ('facts', 'source_ids'),
    ('events', 'source_ids'),
    ('official_status_observations', 'source_ids'),
    ('actor_roles', 'source_ids'),
    ('change_history', 'basis_source_ids'),
    ('deadlines', 'source_ids'),
    ('dossier_relations', 'source_ids'),
)
PRESENTATION_FIELDS = ('group', 'type', 'eyebrow', 'title', 'description', 'status', 'relevance', 'sort')


def parse_temporal(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def valid_https(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return parsed.scheme == 'https' and bool(parsed.hostname) and parsed.username is None and parsed.password is None


def validate_record(record: dict, legacy: dict, *, slug: str) -> list[str]:
    """Return validation messages instead of mutating editorial content."""
    errors: list[str] = []

    def err(message: str) -> None:
        errors.append(f'{slug}: {message}')

    if record.get('schema_version') != 'ndq-dossier-0.2':
        err('unexpected schema_version')
    if not SLUG.fullmatch(slug) or record.get('slug') != slug or legacy.get('id') != slug:
        err('slug mismatch or invalid slug')
    if not record.get('dossier_id') or record.get('dossier_id') == slug:
        err('stable dossier_id required independently of URL slug')
    kind = record.get('kind')
    if kind not in KINDS:
        err('unknown dossier kind')
    elif record.get('presentation', {}).get('group') != KINDS[kind][0]:
        err('presentation.group does not match kind')
    if record.get('publication_status') != 'private_preview_only' or legacy.get('publication_status') != 'private_preview_only':
        err('publication status must remain private_preview_only')
    presentation = record.get('presentation')
    if not isinstance(presentation, dict):
        err('presentation must be object')
        presentation = {}
    for field in PRESENTATION_FIELDS:
        if field not in presentation or not presentation[field]:
            err(f'missing presentation field {field}')
        elif presentation[field] != legacy.get(field):
            err(f'editorial drift in {field} compared with approved v0.1')
    if not parse_temporal(record.get('last_observed_at')):
        err('invalid or missing last_observed_at')
    elif record.get('last_observed_at') != legacy.get('verified_at'):
        err('last_observed_at differs from approved legacy last observation (requires reviewed migration)')

    scope = record.get('scope')
    if not isinstance(scope, dict) or scope.get('sphere') not in ('federal', 'estadual'):
        err('missing/invalid jurisdiction sphere')
    if not record.get('institution'):
        err('institution missing')
    sources = record.get('source_references')
    if not isinstance(sources, list) or not sources:
        err('at least one official source reference required')
        sources = []
    source_ids = set()
    source_urls = set()
    for src in sources:
        sid = src.get('source_id')
        if not isinstance(sid, str) or not sid or sid in source_ids:
            err(f'duplicate or invalid source id: {sid!r}')
        source_ids.add(sid)
        if not valid_https(src.get('url')):
            err(f'invalid source HTTPS URL for {sid}')
        source_urls.add(src.get('url'))
        if not parse_temporal(src.get('observed_at')):
            err(f'invalid source observation date for {sid}')
        if src.get('access_state') not in ('documented_in_prior_review', 'observed_live', 'unavailable', 'not_checked'):
            err(f'invalid access state for {sid}')
    if legacy.get('source') not in source_urls:
        err('v0.1 primary source is missing from normalized source references')
    identities = record.get('official_identities')
    if not isinstance(identities, list) or not identities:
        err('missing official identities')
        identities = []
    identity_keys = set()
    for ident in identities:
        k = (ident.get('institution'), ident.get('namespace'), str(ident.get('value', '')))
        if not all(k) or k in identity_keys:
            err(f'invalid/duplicate official identity {k}')
        identity_keys.add(k)
        if not valid_https(ident.get('reference_url')):
            err(f'official identity {k} has invalid official reference')
        if not parse_temporal(ident.get('verified_at')):
            err(f'official identity {k} has invalid verified_at')

    for field, reference_field in SOURCE_LIST_FIELDS:
        values = record.get(field)
        if not isinstance(values, list):
            err(f'{field} must be a list (empty = not populated, not absent)')
            continue
        for idx, item in enumerate(values):
            refs = item.get(reference_field)
            if not isinstance(refs, list) or not refs:
                err(f'{field}[{idx}] must cite source ids')
            else:
                for ref in refs:
                    if ref not in source_ids:
                        err(f'{field}[{idx}] references unknown source {ref}')
            for fieldname in ('observed_at', 'occurred_at', 'published_at', 'effective_at', 'role_date', 'detected_at', 'verified_at'):
                value = item.get(fieldname)
                if value is not None and not parse_temporal(value):
                    err(f'{field}[{idx}] invalid {fieldname}')
    for field, id_field in [('facts','fact_id'),('events','event_id'),('change_history','change_id')]:
        vals = record.get(field, [])
        if isinstance(vals, list):
            ids = [x.get(id_field) for x in vals]
            if any(not x for x in ids) or len(set(ids)) != len(ids):
                err(f'missing or duplicated {field}.{id_field}')
    if kind == 'legislative_proposal':
        for ev in record.get('events', []):
            if not ev.get('occurred_at') or not ev.get('event_type') or not ev.get('description'):
                err(f'event incomplete: {ev.get("event_id")}')
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}',str(ev.get('occurred_at'))) and ev.get('occurrence_timezone_status') != 'not_provided_by_source':
                err(f'unqualified local time on {ev.get("event_id")}')
    if kind == 'regulatory_agenda_theme':
        ext = record.get('type_data', {})
        if ext.get('normative_effect') != 'NOT_VERIFIED' or ext.get('public_consultation_open') != 'NOT_VERIFIED':
            err('agenda status cannot establish effective regulation or an open consultation')
    if kind == 'public_procurement':
        ext = record.get('type_data', {})
        if ext.get('contract_signed') != 'NOT_VERIFIED' or ext.get('goods_delivered') != 'NOT_VERIFIED':
            err('notice or rectification does not establish contracted/delivered food')
        versions = ext.get('notice_versions', [])
        changes = record.get('change_history', [])
        if len(versions) >= 2 and changes:
            expected = versions[-1].get('maximum_value_brl_cents', 0) - versions[0].get('maximum_value_brl_cents', 0)
            if changes[0].get('delta_brl_cents') != expected or changes[0].get('new_observed_value') != versions[-1].get('maximum_value_brl_cents'):
                err('rectification difference is inconsistent with notice amounts')
    states = record.get('quality_states')
    required_states = ('transport_status','parse_status','identity_status','coverage_status','fact_verification_status','editorial_status')
    if not isinstance(states, dict) or any(not states.get(s) for s in required_states):
        err('quality states must remain independent and explicit')
    assessment = record.get('editorial_assessment')
    if not isinstance(assessment, dict) or assessment.get('review_status') != 'prototype_approved_for_private_integration':
        err('editorial state must not imply public approval or independent verification')
    elif assessment.get('impact_claims') != []:
        err('impact claims not supported in pilot fixtures')
    fact_ids = {x.get('fact_id') for x in record.get('facts', [])}
    for fid in (assessment or {}).get('basis_fact_ids', []):
        if fid not in fact_ids:
            err(f'editorial assessment references unknown fact {fid}')
    return errors


def validate_directory(records_dir: Path = RECORDS, legacy_dir: Path = LEGACY) -> tuple[list[dict], list[str]]:
    results: list[dict] = []
    errors: list[str] = []
    files = sorted(records_dir.glob('*.json'))
    old_files = sorted(legacy_dir.glob('*.json'))
    if not files:
        return [], ['No normalized records found']
    if {x.stem for x in files} != {x.stem for x in old_files}:
        errors.append('Normalized records and legacy HTML fixtures must have matching slug sets')
    seen_dossier_ids = set()
    seen_official_identities = set()
    for path in files:
        try:
            rec = json.loads(path.read_text('utf-8'))
            legacy = json.loads((legacy_dir/path.name).read_text('utf-8'))
        except (OSError, ValueError) as exc:
            errors.append(f'{path.name}: file/JSON error: {exc}')
            continue
        errors.extend(validate_record(rec,legacy,slug=path.stem))
        if rec.get('dossier_id') in seen_dossier_ids:
            errors.append(f'{path.name}: duplicate dossier_id across records')
        seen_dossier_ids.add(rec.get('dossier_id'))
        for ident in rec.get('official_identities',[]):
            idkey=(ident.get('institution'),ident.get('namespace'),str(ident.get('value')))
            if idkey in seen_official_identities:
                errors.append(f'{path.name}: official identity reused in different dossiers without explicit shared-identity design')
            seen_official_identities.add(idkey)
        results.append(rec)
    return results,errors


def main():
    records, errors = validate_directory()
    for issue in errors:
        print('FAIL:',issue)
    if errors:
        print(f'FAIL: {len(errors)} errors across {len(records)} records')
        raise SystemExit(1)
    print(f'PASS: {len(records)} records; {sum(len(d["events"]) for d in records)} events; '
          f'{sum(len(d["source_references"]) for d in records)} source references; private publication gate')


if __name__ == '__main__':
    main()
