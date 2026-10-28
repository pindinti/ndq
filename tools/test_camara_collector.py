"""Synthetic Câmara fixtures only. Every network/DB dependency is injected."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.collectors.http import BASE, FetchResult, JsonClient, RetrievalError, safe_page
from tools.collectors.camara import baseline, service
from tools.collectors.camara.compare import compare_record, compare_snapshots
from tools.collectors.camara.ingestion_plan import plan_source_ingestion, source_content_key
from tools.collectors.camara.normalize import normalize, temporal
from tools.persistence.contract import digest

PID = 42
NOW = '2026-10-27T14:00:00Z'
URL = f'{BASE}/proposicoes/{PID}'


def fixture_data():
    """Invented test values, never a claim about an actual official proposition."""
    event = {'dataHora': '2025-01-02T10:30', 'sequencia': 1, 'siglaOrgao': 'SYNTHETIC',
             'uriOrgao': BASE + '/orgaos/1', 'descricaoTramitacao': 'Synthetic procedural type',
             'codTipoTramitacao': '100', 'despacho': 'Synthetic source description'}
    return {'proposition': {'id': PID, 'uri': URL, 'siglaTipo': 'PL', 'numero': 99, 'ano': 2025,
                            'ementa': 'Synthetic summary', 'dataApresentacao': '2025-01-02T10:30',
                            'statusProposicao': dict(event, descricaoSituacao='Synthetic status')},
            'events': [event],
            'authors': [{'uri': BASE + '/deputados/1', 'nome': 'Synthetic author', 'ordemAssinatura': 1,
                         'proponente': 1, 'codTipo': 10000, 'tipo': 'Deputado(a)'}],
            'relationships': []}


def responses(data=None, *, complete=True):
    data = data or fixture_data()
    return {name: FetchResult(URL + suffix, copy.deepcopy(data[name]), complete,
                              [{'url': URL + suffix, 'retrieved_at': NOW}])
            for name, suffix in service.SUFFIXES.items()}


def observation(data=None, *, complete=True):
    return normalize(PID, responses(data, complete=complete), NOW)


def record():
    return {'dossier_id': 'ndq:synthetic:42', 'slug': 'synthetic-test', 'last_observed_at': '2025-01-03',
            'official_identities': [{'institution': 'camara', 'namespace': 'proposicoes', 'value': str(PID)}],
            'type_data': {'legislative_reference': 'PL 99/2025', 'law_in_force': 'NOT_VERIFIED_AND_NOT_CLAIMED'},
            'facts': [{'predicate': 'official_summary', 'value': 'Synthetic summary'},
                      {'predicate': 'presented_at', 'value': '2025-01-02T10:30'}],
            'events': [{'official_sequence': 1, 'occurred_at': '2025-01-02T10:30', 'agency_code': 'SYNTHETIC',
                        'event_type': 'Synthetic procedural type', 'description': 'Synthetic curated summary'}],
            'official_status_observations': [{'status_text': 'Synthetic status'}],
            'actor_roles': [{'actor_name': 'Synthetic author', 'role_type': 'author'},
                            {'actor_name': 'Preserved rapporteur', 'role_type': 'designated_rapporteur'}],
            'editorial_assessment': {'review_status': 'prototype_approved_for_private_integration'},
            'publication_status': 'private_preview_only'}


def baseline_fixture(value=None):
    value = value or record()
    return {'status': 'matched', 'record': value, 'history': [], 'sha256': digest(value)}


def client_with_payloads(payloads, **kwargs):
    request = Mock(side_effect=[(200, json.dumps(x).encode()) for x in payloads])
    return JsonClient(request=request, sleep=lambda _: None, monotonic=lambda: 0, now=lambda: NOW, **kwargs), request


class HttpCollectorTest(unittest.TestCase):
    def test_valid_proposition_response(self):
        client, _ = client_with_payloads([{'dados': fixture_data()['proposition'], 'links': [{'rel': 'self', 'href': URL}]}])
        response = client.fetch(URL, many=False)
        self.assertTrue(response.complete)
        self.assertEqual(PID, response.data['id'])
        self.assertEqual(NOW, response.pages[0]['retrieved_at'])

    def test_complete_paginated_history(self):
        endpoint = URL + '/tramitacoes'
        next_url = endpoint + '?pagina=2'
        client, request = client_with_payloads([
            {'dados': [fixture_data()['events'][0]], 'links': [{'rel': 'self', 'href': endpoint}, {'rel': 'next', 'href': next_url}]},
            {'dados': [dict(fixture_data()['events'][0], sequencia=2)], 'links': [{'rel': 'self', 'href': next_url}]},
        ])
        result = client.fetch(endpoint, many=True)
        self.assertTrue(result.complete)
        self.assertEqual(2, len(result.data))
        self.assertEqual(2, request.call_count)

    def test_partial_failure_preserves_first_page_and_no_absence_claim(self):
        endpoint = URL + '/tramitacoes'
        request = Mock(side_effect=[(200, json.dumps({'dados': fixture_data()['events'], 'links': [
            {'rel': 'self', 'href': endpoint}, {'rel': 'next', 'href': endpoint + '?pagina=2'}]}).encode()), TimeoutError()])
        result = JsonClient(request=request, retries=0, sleep=lambda _: None).fetch(endpoint, many=True)
        self.assertFalse(result.complete)
        self.assertEqual(1, len(result.data))
        self.assertEqual('timeout_or_network_error', result.issues[0]['code'])

    def test_unsafe_pagination_never_requested(self):
        endpoint = URL + '/tramitacoes'
        for target in ('https://example.invalid/data', endpoint.replace('https:', 'http:'),
                       URL + '/autores?pagina=2', endpoint + '?pagina=3', endpoint + '?pagina=2&dataInicio=2025-01-01'):
            client, request = client_with_payloads([{'dados': [], 'links': [
                {'rel': 'self', 'href': endpoint}, {'rel': 'next', 'href': target}]}])
            with self.subTest(target=target):
                self.assertFalse(client.fetch(endpoint, many=True).complete)
                self.assertEqual(1, request.call_count)

    def test_incomplete_last_page_and_missing_links(self):
        endpoint = URL + '/tramitacoes'
        for links in (None, [], [{'rel': 'self', 'href': endpoint}, {'rel': 'last', 'href': endpoint + '?pagina=2'}]):
            client, _ = client_with_payloads([{'dados': [], 'links': links}])
            self.assertFalse(client.fetch(endpoint, many=True).complete)

    def test_timeout_retry_exhaustion_and_bounded_backoff(self):
        request, sleeps = Mock(side_effect=TimeoutError()), []
        result = JsonClient(request=request, retries=2, sleep=sleeps.append, monotonic=lambda: 0).fetch(URL, many=False)
        self.assertFalse(result.complete)
        self.assertEqual(3, request.call_count)
        self.assertTrue(all(0 <= delay <= 4 for delay in sleeps))

    def test_transient_5xx_retried_and_4xx_not_retried(self):
        good = json.dumps({'dados': fixture_data()['proposition'], 'links': [{'rel': 'self', 'href': URL}]}).encode()
        request = Mock(side_effect=[(503, b''), (200, good)])
        self.assertTrue(JsonClient(request=request, sleep=lambda _: None).fetch(URL, many=False).complete)
        denied = Mock(return_value=(404, b'not retained'))
        result = JsonClient(request=denied, sleep=lambda _: None).fetch(URL, many=False)
        self.assertEqual(1, denied.call_count)
        self.assertEqual(404, result.issues[0]['http_status'])

    def test_invalid_json_wrong_shape_and_redirect_are_incomplete(self):
        for reply in ((200, b'<html>invalid</html>'), (200, b'{"dados":null,"links":[]}'),
                      (200, b'{"dados":{},"dados":{},"links":[]}')):
            self.assertFalse(JsonClient(request=lambda *_: reply).fetch(URL, many=False).complete)
        request = Mock(side_effect=RetrievalError('redirect_refused', 302))
        self.assertEqual('redirect_refused', JsonClient(request=request).fetch(URL, many=False).issues[0]['code'])

    def test_page_limit(self):
        endpoint = URL + '/tramitacoes'
        client, _ = client_with_payloads([{'dados': [], 'links': [
            {'rel': 'self', 'href': endpoint}, {'rel': 'next', 'href': endpoint + '?pagina=2'}]}], max_pages=1)
        self.assertEqual('page_limit', client.fetch(endpoint, many=True).issues[0]['code'])

    def test_equivalent_pagination_query_order_is_canonical(self):
        endpoint = URL + '/tramitacoes'
        self.assertEqual(safe_page(endpoint+'?pagina=2&itens=100', endpoint),
                         safe_page(endpoint+'?itens=100&pagina=2', endpoint))


class NormalizationAndComparisonTest(unittest.TestCase):
    def test_missing_optional_fields(self):
        data = fixture_data()
        del data['proposition']['statusProposicao']
        del data['authors'][0]['uri']
        observed = observation(data)
        self.assertIsNone(observed['proposition']['status_date']['value'])
        self.assertIsNone(observed['authors'][0]['match_key'])
        findings = compare_record(observed, baseline_fixture())
        self.assertTrue(any(f['key'] == 'status' and f['classification'] == 'uncertain' for f in findings))

    def test_previous_snapshot_missing_optional_value_is_uncertain(self):
        data = fixture_data()
        before = observation(data)
        del data['proposition']['statusProposicao']
        findings = compare_snapshots(observation(data), before)
        self.assertTrue(any(f['section'] == 'proposition' and f['classification'] == 'uncertain' for f in findings))

    def test_unknown_precision_future_date_and_publication_are_not_conflated(self):
        data = fixture_data()
        data['events'][0]['dataHora'] = '2025-01'
        self.assertIsNone(observation(data)['events'][0]['occurred_at'])
        data['events'][0]['dataHora'] = 'unknown precision'
        self.assertFalse(observation(data)['coverage']['events']['complete'])
        data['events'][0]['dataHora'] = '2027-01-02T10:30'
        event = observation(data)['events'][0]
        self.assertEqual('future_source_date_unconfirmed', event['event_state'])
        self.assertIsNone(event['occurred_at'])
        self.assertIsNone(event['scheduled_for'])
        self.assertIsNone(event['published_at'])
        self.assertEqual('not_provided_by_source', event['source_date']['timezone_status'])

    def test_invalid_dates_do_not_gain_precision(self):
        for value in ('2025-02-30', '2025-13', '2025-01-02T99:00', '02/01/2025'):
            self.assertEqual('unknown', temporal(value, 'source')['precision'])

    def test_no_full_document_fields_retained_or_law_inferred(self):
        data = fixture_data()
        data['proposition'].update(texto='raw document', justificativa='raw body')
        observed = observation(data)
        self.assertNotIn('texto', observed['proposition']['original_fields'])
        self.assertNotIn('justificativa', observed['proposition']['original_fields'])
        self.assertNotIn('law_in_force', observed['proposition'])
        self.assertEqual('author_as_returned_not_rapporteur', observed['authors'][0]['role'])

    def test_repeated_identical_and_reordered_collection(self):
        data = fixture_data()
        data['events'].append(dict(data['events'][0], sequencia=2))
        before = observation(data)
        data['events'].reverse()
        findings = compare_snapshots(observation(data), before)
        self.assertTrue(findings)
        self.assertTrue(all(f['classification'] == 'unchanged' for f in findings))

    def test_new_event_and_literal_description_correction(self):
        data = fixture_data()
        before = observation(data)
        data['events'][0]['despacho'] = 'Synthetic corrected official description'
        data['events'].append(dict(data['events'][0], sequencia=2))
        findings = compare_snapshots(observation(data), before)
        self.assertTrue(any(f['section'] == 'events' and f['classification'] == 'changed' for f in findings))
        self.assertTrue(any(f['section'] == 'events' and f['classification'] == 'new' for f in findings))

    def test_curated_description_is_separate_from_literal_source_change(self):
        findings = compare_record(observation(), baseline_fixture())
        self.assertFalse(any(f['key'] and str(f['key']).endswith('/description') for f in findings))
        representations = service.editorial_representations(observation(), baseline_fixture())
        self.assertEqual('Synthetic curated summary', representations[0]['curated_summary'])
        self.assertEqual('Synthetic source description', representations[0]['literal_official_despacho'])
        self.assertEqual('distinct_representations_no_source_change_inferred',
                         representations[0]['comparison_status'])

    def test_changed_official_status(self):
        data = fixture_data()
        data['proposition']['statusProposicao']['descricaoSituacao'] = 'Synthetic changed status'
        findings = compare_record(observation(data), baseline_fixture())
        self.assertTrue(any(f['key'] == 'status' and f['classification'] == 'changed' for f in findings))

    def test_duplicate_looking_distinct_events_and_compound_collision(self):
        data = fixture_data()
        data['events'].append(dict(data['events'][0], siglaOrgao='SECOND', uriOrgao=BASE + '/orgaos/2'))
        observed = observation(data)
        self.assertNotEqual(observed['events'][0]['match_key'], observed['events'][1]['match_key'])
        data['events'].append(dict(data['events'][0], despacho='Synthetic conflicting row'))
        findings = compare_record(observation(data), baseline_fixture())
        self.assertTrue(any(f['classification'] == 'conflicting' and f['reason'] == 'compound_identity_collision' for f in findings))

    def test_missing_in_partial_history_is_never_deletion_or_unchanged(self):
        data = fixture_data()
        data['events'] = []
        findings = compare_record(observation(data, complete=False), baseline_fixture())
        event_findings = [f for f in findings if f['section'] == 'events']
        self.assertTrue(event_findings)
        self.assertTrue(all(f['classification'] == 'retrieval_incomplete' for f in event_findings))
        complete = compare_record(observation(data), baseline_fixture())
        self.assertTrue(any(f['reason'] == 'not_returned_not_a_deletion' and f['classification'] == 'uncertain' for f in complete))

    def test_identity_conflict_and_missing_baseline(self):
        old = baseline_fixture()
        old['record']['official_identities'][0]['value'] = '999'
        self.assertEqual('conflicting', compare_record(observation(), old)[0]['classification'])
        data = fixture_data()
        data['proposition']['id'] = 999
        self.assertIsNone(observation(data)['proposition'])
        self.assertEqual('conflicting', compare_record(observation(data), baseline_fixture())[0]['classification'])
        self.assertEqual('retrieval_incomplete', compare_record(observation(), {'status': 'not_found'})[0]['classification'])

    def test_event_seen_in_history_is_not_unqualified_new(self):
        old = baseline_fixture()
        old['history'] = [copy.deepcopy(old['record'])]
        old['record']['events'] = []
        self.assertTrue(any(f['classification'] == 'uncertain' and f['section'] == 'events'
                            for f in compare_record(observation(), old)))

    def test_related_proposition_is_review_candidate_without_legal_type(self):
        data = fixture_data()
        data['relationships'] = [{'id': 43, 'uri': BASE + '/proposicoes/43', 'siglaTipo': 'REQ',
                                  'numero': 1, 'ano': 2025, 'ementa': 'Synthetic related item'}]
        observed = observation(data)
        relation = observed['relationships'][0]
        self.assertEqual('returned_as_related_unspecified', relation['relationship_type'])
        self.assertIsNone(relation['legal_effect'])
        self.assertEqual('pending_review', relation['review_state'])
        self.assertTrue(any(f['reason'] == 'official_related_candidate_type_unspecified'
                            for f in compare_record(observed, baseline_fixture())))

    def test_untrusted_urls_are_not_retained_or_exposed(self):
        data = fixture_data()
        data['authors'][0]['uri'] = 'https://user:do-not-retain@example.invalid/author'
        observed = observation(data)
        self.assertFalse(observed['coverage']['authors']['complete'])
        self.assertEqual([], observed['authors'])
        self.assertNotIn('do-not-retain', json.dumps(observed))

    def test_related_identity_collision(self):
        data = fixture_data()
        related = {'id': 43, 'uri': BASE + '/proposicoes/43'}
        data['relationships'] = [related, dict(related)]
        self.assertTrue(any(f['classification'] == 'conflicting' and f['section'] == 'relationships'
                            for f in compare_record(observation(data), baseline_fixture())))


class DatabaseAndProposalTest(unittest.TestCase):
    def test_database_unavailable_is_sanitized_and_prevents_global_complete(self):
        request = Mock(side_effect=RuntimeError('password=must-not-print private-endpoint'))
        database = baseline.database_baseline(PID, config_reader=lambda: {}, connect=request)
        self.assertEqual('unavailable', database['status'])
        self.assertNotIn('must-not-print', json.dumps(database))
        client = SimpleNamespace(fetch=lambda url, many: responses()[next(k for k,s in service.SUFFIXES.items() if url == URL+s)])
        report = service.collect(PID, client=client, database_loader=lambda _: database,
                                 local_loader=lambda _: baseline_fixture(), now=lambda: NOW)
        self.assertEqual('partial', report['execution_status'])
        self.assertIn('retrieval_incomplete', report['finding_counts'])

    def test_target_guard_failure_prevents_any_connection(self):
        connect = Mock()
        result = baseline.database_baseline(PID, config_reader=Mock(side_effect=ValueError('protected target')), connect=connect)
        connect.assert_not_called()
        self.assertEqual('unavailable', result['status'])

    def test_database_read_only_queries_preserve_review_publication_and_record(self):
        value = record()
        original = copy.deepcopy(value)
        queries = []
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchone(self): return self.rows[0]
            def fetchall(self): return self.rows
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def transaction(self): return nullcontext()
            def execute(self, query, params=()):
                queries.append(query)
                if query.split()[0] not in ('SELECT', 'SHOW', 'SET'):
                    raise AssertionError('Database write attempted')
                if query.startswith('SET '): return Result([])
                if query.startswith('SHOW '): return Result([('on',)])
                if 'current_database()' in query: return Result([('neondb',)])
                if 'official_identities' in query: return Result([(value['dossier_id'],)])
                if 'dossier_revisions' in query: return Result([('synthetic-revision', 1, digest(value), '2025-01-03')])
                return Result([('pending_review',)])
        with patch.object(baseline, 'client_tls_details', return_value={'tls_active': True}), \
             patch.object(baseline, 'read_record', return_value=value):
            db = baseline.database_baseline(PID, config_reader=lambda: {}, connect=lambda _: Connection())
        self.assertEqual('matched', db['status'])
        self.assertTrue(db['read_only'])
        self.assertEqual('pending_review', db['review_state'])
        self.assertEqual(original, value)
        self.assertEqual('private_preview_only', baseline.summary(db)['publication_state'])
        self.assertTrue(any('READ ONLY' in q for q in queries))

    def test_read_only_connection_starts_with_readonly_option(self):
        connect = Mock()
        with patch.dict('sys.modules', {'psycopg': SimpleNamespace(connect=connect)}):
            baseline._connect({'url': 'synthetic-uri', 'sslmode': 'require'})
        self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])

    def test_dry_run_no_database_mode_never_loads_credentials(self):
        client = SimpleNamespace(fetch=lambda url, many: responses()[next(k for k,s in service.SUFFIXES.items() if url == URL+s)])
        db = Mock(side_effect=AssertionError('Must not access database'))
        report = service.collect(PID, client=client, use_database=False, database_loader=db,
                                 local_loader=lambda _: baseline_fixture(), now=lambda: NOW)
        db.assert_not_called()
        self.assertFalse(report['database_writes'])
        self.assertFalse(report['publication_approved'])
        event_findings = [f for f in report['comparisons']['local_record'] if f['section'] == 'events']
        self.assertTrue(all(f['official_locator']['source_rows'][0]['sequencia'] == 1 for f in event_findings))
        self.assertTrue(all(f['retrieved_at'] == NOW for f in event_findings))

    def test_baseline_divergence_is_conflicting_without_merging(self):
        client = SimpleNamespace(fetch=lambda url, many: responses()[next(k for k,s in service.SUFFIXES.items() if url == URL+s)])
        local, db = baseline_fixture(), baseline_fixture()
        db['record']['events'][0]['description'] = 'Synthetic different revision'
        db['sha256'] = digest(db['record'])
        report = service.collect(PID, client=client, local_loader=lambda _: local,
                                 database_loader=lambda _: db, now=lambda: NOW)
        self.assertEqual('partial', report['execution_status'])
        self.assertEqual('conflicting', report['comparisons']['baseline_alignment'][0]['classification'])

    def test_private_report_and_previous_snapshot_roundtrip(self):
        client = SimpleNamespace(fetch=lambda url, many: responses()[next(k for k,s in service.SUFFIXES.items() if url == URL+s)])
        report = service.collect(PID, client=client, use_database=False,
                                 local_loader=lambda _: baseline_fixture(), now=lambda: NOW)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(service, 'ROOT', root), patch.object(service, 'REPORT_ROOT', root/'.ndq-private'/'camara'):
                path = service.save_report(report)
                previous = service.previous_observation(path, PID)
                findings = compare_snapshots(report['observation'], previous)
                self.assertTrue(all(f['classification'] == 'unchanged' for f in findings))
                with self.assertRaises(ValueError):
                    service.previous_observation(root/'public.json', PID)


PRIVATE_PILOT = Path(__file__).resolve().parents[1] / '.ndq-private' / 'camara' / '2498778-20261027T235638Z-d870e8d4.json'
LIVE_PILOT_ID = 2498778


@unittest.skipUnless(PRIVATE_PILOT.is_file(), 'Ignored private proposal unavailable in this checkout')
class PrivatePilotRegressionTest(unittest.TestCase):
    """Reads an ignored local proposal; never makes HTTP or database calls."""

    def setUp(self):
        self.previous = service.previous_observation(PRIVATE_PILOT, LIVE_PILOT_ID)

    def source_rows(self):
        return {name: (copy.deepcopy(self.previous[name]['original_fields']) if name == 'proposition'
                       else [copy.deepcopy(item['original_fields']) for item in self.previous[name]])
                for name in service.SUFFIXES}

    def normalized(self, rows, *, observed_at='2026-10-28T14:00:00Z'):
        fetched = {name: FetchResult(f'{BASE}/proposicoes/{LIVE_PILOT_ID}{suffix}', rows[name], True)
                   for name, suffix in service.SUFFIXES.items()}
        return normalize(LIVE_PILOT_ID, fetched, observed_at)

    def test_curated_and_official_descriptions_remain_distinct(self):
        local = baseline.local_baseline(LIVE_PILOT_ID)
        representations = service.editorial_representations(self.previous, local)
        self.assertEqual(7, len(representations))
        self.assertTrue(all(x['comparison_status'] == 'distinct_representations_no_source_change_inferred'
                            for x in representations))
        self.assertFalse(any(x['key'] and str(x['key']).endswith('/description')
                             for x in compare_record(self.previous, local)))
        self.assertTrue(all(x['classification'] == 'unchanged'
                            for x in compare_snapshots(self.normalized(self.source_rows()), self.previous)))

    def test_author_uri_is_sourced_and_unlinked_not_rapporteur(self):
        author = self.previous['authors'][0]
        self.assertEqual(author['original_fields']['uri'], author['official_identity']['value'])
        self.assertEqual('author_uri', author['official_identity']['namespace'])
        self.assertEqual('unresolved', author['official_identity']['link_to_legacy_actor'])
        self.assertEqual('author_as_returned_not_rapporteur', author['role'])
        local = baseline.local_baseline(LIVE_PILOT_ID)
        self.assertTrue(all(x['reason'] != 'baseline_name_does_not_resolve_official_actor_identity'
                            for x in compare_record(self.previous, local)))

    def test_complete_empty_related_endpoint_is_not_recurring_uncertainty(self):
        self.assertTrue(self.previous['coverage']['relationships']['complete'])
        self.assertEqual(0, self.previous['coverage']['relationships']['returned_count'])
        self.assertEqual([], self.previous['relationships'])
        local = baseline.local_baseline(LIVE_PILOT_ID)
        self.assertFalse(any(x['section'] == 'relationships' for x in compare_record(self.previous, local)))
        repeated = self.normalized(self.source_rows())
        self.assertFalse(any(x['section'] == 'relationships' for x in compare_snapshots(repeated, self.previous)))

    def test_midnight_source_value_is_not_exact_occurrence_or_publication(self):
        source_midnight = [x for x in self.previous['events']
                           if x['original_fields']['dataHora'].endswith('T00:00')]
        self.assertTrue(source_midnight)
        self.assertTrue(all(x['occurred_at'] is None for x in source_midnight))
        rows = self.source_rows()
        rows['events'][0]['dataHora'] = rows['events'][0]['dataHora'][:10] + 'T00:00'
        current = self.normalized(rows)
        event = next(x for x in current['events'] if x['original_fields']['dataHora'].endswith('T00:00'))
        self.assertEqual(rows['events'][0]['dataHora'], event['source_date']['value'])
        self.assertIsNone(event['occurred_at'])
        self.assertIsNone(event['published_at'])
        self.assertIsNone(event['scheduled_for'])
        self.assertEqual('source_reported_procedural_date_not_exact_occurrence', event['event_state'])
        self.assertNotEqual(current['retrieved_at'], event['source_date']['value'])

    def test_retention_and_field_endpoint_comparison_scope(self):
        rows = self.source_rows()
        rows['proposition']['texto'] = 'fixture raw document must be dropped'
        rows['proposition']['unreviewed_field'] = 'fixture value must be dropped'
        current = self.normalized(rows)
        self.assertFalse('fixture raw document must be dropped' in json.dumps(current))
        self.assertFalse('fixture value must be dropped' in json.dumps(current))
        policy = current['coverage']['proposition']['field_policy']
        self.assertEqual(2, policy['unretained_field_occurrences'])
        self.assertEqual('selected_fields_only_not_complete_change_detection', policy['comparison_scope'])
        self.assertIn('despacho', current['coverage']['events']['field_policy']['compared_with_prior_source'])
        self.assertIn('uri', current['coverage']['authors']['field_policy']['retained'])
        self.assertIn('id', current['coverage']['relationships']['field_policy']['compared_with_prior_source'])
        self.assertEqual(source_content_key(self.previous), source_content_key(current))
        self.assertTrue(all(x['classification'] == 'unchanged' for x in compare_snapshots(current, self.previous)))

    def test_literal_change_is_field_specific_and_prior_source_based(self):
        rows = self.source_rows()
        rows['events'][0]['despacho'] += ' fixture correction'
        current = self.normalized(rows)
        differences = [x for x in compare_snapshots(current, self.previous) if x['classification'] == 'changed']
        self.assertEqual(['despacho'], [x['field_path'] for x in differences])
        self.assertTrue(all(x['reason'] == 'selected_official_field_changed' for x in differences))
        self.assertFalse(any('curated' in x['reason'] for x in differences))

    def test_append_only_plan_deduplicates_identical_weekly_source(self):
        repeated = self.normalized(self.source_rows(), observed_at='2026-11-03T14:00:00Z')
        key = source_content_key(self.previous)
        self.assertEqual(key, source_content_key(repeated))
        plan = plan_source_ingestion(repeated, known_content_keys=(key,), previous=self.previous)
        self.assertEqual('already_observed', plan['status'])
        self.assertFalse(plan['append_source_snapshot'])
        self.assertEqual([], plan['source_change_candidates'])
        self.assertFalse(plan['create_factual_revision'])
        self.assertFalse(plan['create_editorial_proposal'])
        self.assertFalse(plan['approve_or_publish'])
        self.assertEqual('already_observed', plan_source_ingestion(repeated, previous=self.previous)['status'])

    def test_changed_source_proposes_only_review_candidates(self):
        rows = self.source_rows()
        rows['events'][0]['despacho'] += ' fixture correction'
        changed = self.normalized(rows)
        plan = plan_source_ingestion(changed, known_content_keys=(source_content_key(self.previous),),
                                     previous=self.previous)
        self.assertEqual('new_source_snapshot', plan['status'])
        self.assertTrue(plan['append_source_snapshot'])
        self.assertEqual(['despacho'], [x['field_path'] for x in plan['source_change_candidates']])
        self.assertFalse(plan['create_factual_revision'])
        self.assertFalse(plan['create_editorial_proposal'])
        self.assertFalse(plan['approve_or_publish'])
        partial = copy.deepcopy(changed)
        partial['coverage']['events']['complete'] = False
        self.assertEqual('incomplete_or_invalid', plan_source_ingestion(partial)['status'])

    def test_full_offline_report_uses_prior_source_without_repeated_alert(self):
        rows = self.source_rows()
        fetched = {name: FetchResult(f'{BASE}/proposicoes/{LIVE_PILOT_ID}{suffix}', rows[name], True)
                   for name, suffix in service.SUFFIXES.items()}
        client = SimpleNamespace(fetch=lambda endpoint, many: fetched[next(
            name for name, suffix in service.SUFFIXES.items()
            if endpoint == f'{BASE}/proposicoes/{LIVE_PILOT_ID}{suffix}')])
        report = service.collect(LIVE_PILOT_ID, client=client, use_database=False,
                                 previous=self.previous, now=lambda: '2026-11-03T14:00:00Z')
        self.assertEqual('already_observed', report['source_ingestion_plan']['status'])
        self.assertEqual([], report['source_ingestion_plan']['source_change_candidates'])
        self.assertEqual('contextual_curated_baseline_not_source_change_alerts',
                         report['comparison_purposes']['local_record'])
        self.assertFalse(report['database_writes'])


if __name__ == '__main__':
    unittest.main()
