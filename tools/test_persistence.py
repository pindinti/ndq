"""Offline tests: standard library only, no credentials, driver or connection needed."""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tools.persistence.__main__ import main
from tools.persistence.contract import (ContractError, LEGACY, RECORDS, digest, item_key,
                                        load_pilots, summary, temporal, validate)
from tools.persistence import db
from tools.persistence.db import connect, load_database_url
from tools.persistence.repository import schedule_check


def case(slug='pl-1777-2025'):
    filename = slug + '.json'
    return (json.loads((RECORDS / filename).read_text('utf-8')),
            json.loads((LEGACY / filename).read_text('utf-8')))


class PilotContractTest(unittest.TestCase):
    def reject(self, change, slug='pl-1777-2025'):
        r, legacy = case(slug)
        change(r)
        with self.assertRaises(ContractError):
            validate(r, legacy)

    def test_pilots_validate_without_mutation(self):
        for p in load_pilots():
            before = copy.deepcopy(p.record)
            _, legacy = case(p.record['slug'])
            validate(p.record, legacy)
            self.assertEqual(before, p.record)
        counts = summary(load_pilots())['counts']
        self.assertEqual((4, 15, 10, 9, 7), tuple(counts[k] for k in
                         ('official_identities', 'source_references', 'facts', 'events', 'contextual_milestones')))

    def test_dry_run_never_connects_even_if_url_is_set(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://must-not-connect/ndq'}), \
                patch('tools.persistence.__main__.connect', side_effect=AssertionError('Unexpected connection')), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, main(['import-pilots', '--dry-run']))
        self.assertTrue(json.loads(out.getvalue())['success'])

    def test_report_written_offline(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            report = Path(tmp) / 'report.json'
            self.assertEqual(0, main(['import-pilots', '--dry-run', '--report', str(report)]))
            data = json.loads(report.read_text('utf-8'))
            self.assertEqual('pending_review', data['review_state'])
            self.assertEqual(3, data['dossiers'])

    def test_database_url_required_without_libpq_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, 'ROOT', Path(tmp)), \
                patch.dict(sys.modules, {'dotenv': types.SimpleNamespace(load_dotenv=lambda **kwargs: False)}), \
                patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'DATABASE_URL'):
                connect()

    def test_report_file_failure_preserves_actual_operation_result(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(1, main(['import-pilots', '--dry-run', '--report', str(Path(tmp) / 'missing' / 'report.json')]))
        result = json.loads(out.getvalue())
        self.assertTrue(result['success'])
        self.assertEqual('FileNotFoundError', result['report_file_error'])

    def test_connection_error_does_not_expose_credentials(self):
        with patch('tools.persistence.__main__.load_database_url', return_value='postgresql://safe.invalid/db'), \
                patch('tools.persistence.__main__.connect', side_effect=RuntimeError('password=secret-value')), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(1, main(['migrate']))
        self.assertNotIn('secret-value', out.getvalue())

    def test_canonical_hash_ignores_object_order_but_preserves_array_order(self):
        self.assertEqual(digest({'a': 1, 'b': 2}), digest({'b': 2, 'a': 1}))
        self.assertNotEqual(digest([1, 2]), digest([2, 1]))

    def test_explicit_unknowns_and_empty_lists_remain_unchanged(self):
        r, _ = case('anvisa-plant-based')
        self.assertEqual('NOT_VERIFIED', r['type_data']['normative_effect'])
        self.assertEqual([], r['events'])
        self.assertIsNone(r['created_at'])
        r, _ = case('fundepar-pao-de-queijo-vegano')
        self.assertEqual('NOT_VERIFIED', r['type_data']['goods_delivered'])

    def test_unknown_schema_rejected(self):
        self.reject(lambda r: r.update(schema_version='ndq-dossier-99'))

    def test_unknown_or_raw_response_fields_rejected(self):
        for key in ('raw_response', 'html', 'pdf_bytes'):
            with self.subTest(key=key):
                self.reject(lambda r: r['source_references'][0].update({key: 'body'}))

    def test_raw_document_bodies_and_response_objects_rejected(self):
        self.reject(lambda r: r['facts'][0].update(value='<!DOCTYPE html><html>full document</html>'))
        self.reject(lambda r: r['facts'][0].update(value={'dados': {'entire': 'response'}}))
        self.reject(lambda r: r['facts'][0].update(value='x' * 16001))

    def test_nan_rejected(self):
        self.reject(lambda r: r['facts'][0].update(value=float('nan')))

    def test_invalid_shapes_rejected_as_contract_errors(self):
        for section in ('facts', 'source_references', 'actor_roles'):
            with self.subTest(section=section):
                self.reject(lambda r: r.update({section: ['invalid']}))

    def test_duplicate_event_identity_rejected(self):
        self.reject(lambda r: r['events'].append(copy.deepcopy(r['events'][0])))

    def test_official_identity_needs_stable_nonempty_components(self):
        self.reject(lambda r: r['official_identities'][0].update(value=None))

    def test_missing_provenance_rejected(self):
        self.reject(lambda r: r['facts'][0].update(source_ids=[]))
        self.reject(lambda r: r['events'][0].update(source_ids=['unknown']))
        self.reject(lambda r: r['facts'][0].update(source_ids=['camara-proposicao'] * 2))

    def test_unknown_source_state_not_promoted_to_checked(self):
        r, old = case()
        validate(r, old)
        self.assertEqual('not_checked', r['source_references'][-1]['access_state'])

    def test_no_implicit_timezone_conversion(self):
        r, old = case()
        validate(r, old)
        self.assertEqual('2025-04-22T15:50', r['events'][0]['occurred_at'])
        self.assertIsNone(r['events'][0]['published_at'])
        self.reject(lambda r: r['events'][0].update(occurrence_timezone_status='offset_provided'))

    def test_future_occurrence_rejected(self):
        self.reject(lambda r: r['events'][0].update(occurred_at='2027-01-01T15:50'))

    def test_procurement_schedule_or_delivery_cannot_become_documentary_event(self):
        self.reject(lambda r: r['events'][0].update(event_type='scheduled_auction'), 'fundepar-pao-de-queijo-vegano')
        self.reject(lambda r: r['events'][0].update(event_type='goods_delivered'), 'fundepar-pao-de-queijo-vegano')

    def test_month_precision_and_scheduled_context_preserved(self):
        r, _ = case('anvisa-plant-based')
        self.assertEqual('2021-10', r['contextual_milestones'][0]['period'])
        self.reject(lambda r: r['contextual_milestones'][0].update(period='2021-10-01'), 'anvisa-plant-based')
        self.reject(lambda r: r['contextual_milestones'][1].update(occurred_at='2026-06-11'), 'fundepar-pao-de-queijo-vegano')

    def test_temporal_parser_rejects_invalid_calendar_or_precision(self):
        for value in ('2026-02-30', '2026-10-27T24:00', '2026-10-27 12:00', '2026', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                temporal(value)
        for value in ('2026-10-27', '2026-10-27T08:30', '2026-10-27T08:30:00-03:00'):
            temporal(value)
        temporal('2026-10', month=True)

    def test_unestablished_populated_contracts_rejected(self):
        for section in ('dossier_relations', 'deadlines', 'coverage_evidence'):
            with self.subTest(section=section):
                self.reject(lambda r: r.update({section: [{'source_ids': ['camara-proposicao']}]}))

    def test_public_approval_or_invented_reviewer_rejected(self):
        self.reject(lambda r: r.update(publication_status='published'))
        self.reject(lambda r: r['editorial_assessment'].update(review_status='approved_public'))
        self.reject(lambda r: r['editorial_assessment'].update(reviewer='invented'))

    def test_documented_change_requires_both_known_sources(self):
        self.reject(lambda r: r['change_history'][0].update(basis_source_ids=['unknown']), 'fundepar-pao-de-queijo-vegano')

    def test_missing_id_uses_explicit_local_content_key(self):
        r, _ = case()
        role = r['actor_roles'][0]
        key = item_key('actor_roles', role)
        self.assertIn('/local-content:', key)
        self.assertEqual(key, item_key('actor_roles', dict(reversed(list(role.items())))))

    def test_reused_identity_in_two_dossiers_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = Path(tmp) / 'records'
            legacy = Path(tmp) / 'legacy'
            records.mkdir()
            legacy.mkdir()
            for i, slug in enumerate(('pl-1777-2025', 'anvisa-plant-based')):
                r, old = case(slug)
                if i:
                    other, _ = case()
                    ident = copy.deepcopy(other['official_identities'][0])
                    ident['source_ids'] = [r['source_references'][0]['source_id']]
                    r['official_identities'] = [ident]
                (records / (slug + '.json')).write_text(json.dumps(r), encoding='utf-8')
                (legacy / (slug + '.json')).write_text(json.dumps(old), encoding='utf-8')
            with self.assertRaisesRegex(ContractError, 'reused'):
                load_pilots(records, legacy)

    def test_schedule_rejects_unqualified_time_before_database_use(self):
        with self.assertRaisesRegex(ValueError, 'timezone-aware'):
            schedule_check(None, revision_id='unused', basis_key='unused', due_at=datetime(2026, 10, 28),
                           reason='Documented check', idempotency_key='unused')


if __name__ == '__main__':
    unittest.main(verbosity=2)
