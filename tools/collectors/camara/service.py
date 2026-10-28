"""Compose retrieval and comparisons into a local, explicitly unapproved report."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from tools.collectors.http import BASE, FetchResult, JsonClient, utc_now
from tools.persistence.db import ROOT
from .baseline import database_baseline, local_baseline, summary
from .compare import compare_record, compare_snapshots, finding
from .ingestion_plan import plan_source_ingestion, source_content_key
from .normalize import normalize

REPORT_ROOT = ROOT / '.ndq-private' / 'camara'
SCHEMA = 'ndq-camara-proposal-0.4.2'
SUFFIXES = {'proposition': '', 'events': '/tramitacoes', 'authors': '/autores', 'relationships': '/relacionadas'}
GAPS = [
    'Existing pilot event descriptions are curated summaries, not previous literal despachos; differences alone do not prove official corrections.',
    'Existing name-only actor roles do not establish identity equivalence with official author URIs; authors are not rapporteurs.',
    'The relacionadas endpoint does not by itself specify a legal relationship type or prove identity equivalence.',
    'The pilot import adapter rejects populated dossier_relations; collector observations are not supported ingestion inputs.',
    'API dataHora values are source procedural dates, not proof of an exact occurrence instant; publication dates and scheduling semantics are not supplied separately by these mappings.',
    'An endpoint with all returned pages does not establish exhaustive institutional history or an atomic snapshot across endpoints.',
    'Only the explicitly retained official fields are compared; unreviewed response fields are dropped, so complete change detection is not claimed.',
    'A future append-only SQL adapter and unique content key are required before source ingestion; this report makes no database changes.',
]


def locate_findings(report: dict) -> None:
    """Attach official row locators so reviewers need not interpret a hash key."""
    observation = report['observation']
    groups = list(report['comparisons'].values())
    groups.append(report['source_ingestion_plan']['source_change_candidates'])
    for findings in groups:
        for item in findings:
            locator = {'proposition_id': observation['proposition_id']}
            key = item.get('key')
            if item['section'] == 'events':
                base_key = key.split('/')[0] if key else None
                rows = [event['original_fields'] for event in observation['events'] if event['match_key'] == base_key]
                if not rows and isinstance(item.get('before'), dict):
                    old = item['before']
                    rows = [old if 'dataHora' in old else {'sequencia': old.get('official_sequence'),
                            'dataHora': old.get('occurred_at'), 'siglaOrgao': old.get('agency_code')}]
                locator['source_rows'] = [{field: row.get(field) for field in ('sequencia', 'dataHora', 'siglaOrgao')}
                                          for row in rows]
            elif item['section'] == 'authors':
                locator['author_uri'] = key
            elif item['section'] == 'relationships':
                locator['related_proposition_id'] = key
            item['official_locator'] = locator
            item['retrieved_at'] = observation['retrieved_at']
            item['retrieval_time_scope'] = 'collection_completed_at'


def editorial_representations(observation: dict, baseline: dict) -> list[dict]:
    """Keep curated summaries beside literal source text, without diffing them."""
    record = baseline.get('record')
    if baseline.get('status') != 'matched' or not record:
        return []
    from .normalize import event_key
    from collections import defaultdict
    old = defaultdict(list)
    for row in record['events']:
        key = event_key(observation['proposition_id'], row.get('official_sequence'),
                        row.get('occurred_at'), row.get('agency_code'))
        if key:
            old[key].append(row)
    result = []
    for event in observation['events']:
        curated = old.get(event['match_key'], [])
        if len(curated) == 1:
            result.append({'match_key': event['match_key'], 'curated_summary': curated[0].get('description'),
                           'literal_official_despacho': event['original_fields'].get('despacho'),
                           'comparison_status': 'distinct_representations_no_source_change_inferred',
                           'source_url': event['evidence_url']})
    return result


def previous_observation(path: Path, proposition_id: int) -> dict:
    resolved = path.resolve()
    if not resolved.is_relative_to(REPORT_ROOT.resolve()) or path.stat().st_size > 5_000_000:
        raise ValueError('Previous proposal must be a bounded private local report')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported previous report')
    previous = report['observation']
    if previous['proposition_id'] != proposition_id:
        raise ValueError('Previous proposition identity mismatch')
    fetched = {}
    for name, suffix in SUFFIXES.items():
        endpoint = f'{BASE}/proposicoes/{proposition_id}{suffix}'
        values = previous[name]
        data = values['original_fields'] if name == 'proposition' and values else None if name == 'proposition' else [v['original_fields'] for v in values]
        complete = previous['coverage'][name]['complete']
        if not isinstance(complete, bool):
            raise ValueError('Invalid previous coverage')
        # Revalidate field allowlists, URLs, dates and IDs rather than trusting
        # computed keys or arbitrary report fields. Previous reports are not signed evidence.
        fetched[name] = FetchResult(endpoint, data, complete)
    reconstructed = normalize(proposition_id, fetched, previous['retrieved_at'])
    for coverage in reconstructed['coverage'].values():
        coverage['field_policy']['unretained_field_occurrences'] = None
        coverage['field_policy']['field_inventory_origin'] = 'prior_projection_only_unknown_fields_unavailable'
        coverage['pages'] = []  # The old envelope is not re-fetched or authenticated.
    return reconstructed


def collect(proposition_id: int, *, client=None, use_database=True, previous=None,
            local_loader=local_baseline, database_loader=database_baseline, now=utc_now) -> dict:
    if isinstance(proposition_id, bool) or not isinstance(proposition_id, int) or proposition_id < 1:
        raise ValueError('Positive official proposition ID required')
    client = client or JsonClient()
    fetched = {name: client.fetch(f'{BASE}/proposicoes/{proposition_id}{suffix}', many=name != 'proposition')
               for name, suffix in SUFFIXES.items()}
    observation = normalize(proposition_id, fetched, now())
    try:
        local = local_loader(proposition_id)
    except Exception as exc:
        local = {'status': 'unavailable', 'record': None, 'history': [], 'error_type': type(exc).__name__}
    database = database_loader(proposition_id) if use_database else {'status': 'disabled', 'record': None, 'history': []}
    comparisons = {'local_record': compare_record(observation, local)}
    if use_database:
        comparisons['database_latest_and_history'] = compare_record(observation, database)
    if previous is not None:
        comparisons['previous_local_source_proposal'] = compare_snapshots(observation, previous)
    previous_keys = ()
    if previous is not None:
        try:
            previous_keys = (source_content_key(previous),)
        except (KeyError, TypeError, ValueError):
            pass
    ingestion_plan = plan_source_ingestion(observation, known_content_keys=previous_keys, previous=previous)
    baseline_conflict = False
    if local['status'] == database['status'] == 'matched':
        baseline_conflict = (local['record']['dossier_id'] != database['record']['dossier_id'] or local['sha256'] != database['sha256'])
        if baseline_conflict:
            comparisons['baseline_alignment'] = [finding('conflicting', 'baseline', str(proposition_id),
                'local_record_differs_from_database_no_automatic_merge', f'{BASE}/proposicoes/{proposition_id}',
                before=local['sha256'], after=database['sha256'])]
    all_findings = [f for findings in comparisons.values() for f in findings]
    complete = all(c['complete'] for c in observation['coverage'].values())
    baseline_ok = (database['status'] == 'matched' if use_database else local['status'] == 'matched')
    report = {'schema_version': SCHEMA, 'mode': 'dry_run_proposal_only', 'publication_approved': False,
            'report_prepared_at': now(),
            'editorial_review_state': 'pending_review', 'database_writes': False,
            'execution_status': 'complete' if complete and baseline_ok and not baseline_conflict else 'partial',
            'observation': observation, 'baselines': {'local': summary(local), 'database': summary(database)},
            'comparisons': comparisons,
            'comparison_purposes': {'local_record': 'contextual_curated_baseline_not_source_change_alerts',
                                    'database_latest_and_history': 'contextual_curated_baseline_not_source_change_alerts',
                                    'previous_local_source_proposal': 'selected_official_fields_against_prior_source_only'},
            'finding_counts': dict(sorted(Counter(f['classification'] for f in all_findings).items())),
            'editorial_representations': editorial_representations(observation, local),
            'source_ingestion_plan': ingestion_plan,
            'model_gaps': GAPS,
            'suggested_review_items': [
                'Conferir os campos oficiais e sua data de observação antes de aceitar qualquer alteração.',
                'Revisar colisões e datas incertas; preservar resumo curatorial e despacho literal como representações distintas.',
                'Conferir o vínculo endpoint/branch no console Neon; o guard usa IDs locais, sem consulta à API Neon.',
                'Manter relações como candidatas e preservar separação entre verificação factual, revisão editorial e publicação.',
            ]}
    locate_findings(report)
    return report


def save_report(report: dict) -> Path:
    expected = ROOT.resolve() / '.ndq-private' / 'camara'
    if REPORT_ROOT.resolve() != expected:
        raise ValueError('Private output symlink or location refused')
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    pid = report['observation']['proposition_id']
    stamp = report['observation']['retrieved_at'].replace(':', '').replace('-', '')
    path = REPORT_ROOT / f'{pid}-{stamp}-{uuid4().hex[:8]}.json'
    output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if len(output.encode('utf-8')) > 5_000_000:
        raise ValueError('Private report size limit exceeded')
    with path.open('x', encoding='utf-8') as stream:
        stream.write(output)
    return path
