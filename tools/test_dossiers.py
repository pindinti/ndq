#!/usr/bin/env python3
"""Offline regression tests for the incremental ndq-dossier-0.2 data contract."""
from __future__ import annotations
import copy
import json
import unittest
from pathlib import Path
from validate_dossiers import RECORDS, LEGACY, validate_record, validate_directory


def case(slug):
    rec=json.loads((RECORDS/(slug+'.json')).read_text(encoding='utf-8'))
    old=json.loads((LEGACY/(slug+'.json')).read_text(encoding='utf-8'))
    return rec,old


class StructuredDossierTest(unittest.TestCase):
    def test_three_fixtures_are_consistent(self):
        rows, errors=validate_directory()
        self.assertEqual([], errors)
        self.assertEqual(3,len(rows))

    def test_stable_identity_is_independent_of_slug(self):
        rec,old=case('pl-1777-2025')
        rec['dossier_id']=rec['slug']
        self.assertTrue(any('stable dossier_id' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_missing_documentary_source_fails(self):
        rec,old=case('fundepar-pao-de-queijo-vegano')
        rec['source_references']=rec['source_references'][:-1]
        rec['facts'][0]['source_ids']=['nonexistent-source']
        self.assertTrue(any('unknown source' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_presentation_drift_fails(self):
        rec,old=case('anvisa-plant-based')
        rec['presentation']['title']='Regulação já aprovada'
        self.assertTrue(any('editorial drift in title' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_no_automatic_publication(self):
        rec,old=case('anvisa-plant-based')
        rec['publication_status']='public'
        self.assertTrue(any('publication status' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_agenda_is_not_automatically_regulation(self):
        rec,old=case('anvisa-plant-based')
        rec['type_data']['normative_effect']='IN_FORCE'
        self.assertTrue(any('agenda status cannot establish' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_fundepar_notice_is_not_delivery(self):
        rec,old=case('fundepar-pao-de-queijo-vegano')
        rec['type_data']['goods_delivered']='YES'
        self.assertTrue(any('contracted/delivered' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_documentary_rectification_must_balance(self):
        rec,old=case('fundepar-pao-de-queijo-vegano')
        rec['change_history'][0]['delta_brl_cents']=-123
        self.assertTrue(any('rectification difference' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_time_without_official_timezone_must_be_flagged(self):
        rec,old=case('pl-1777-2025')
        rec['events'][0]['occurrence_timezone_status']='America/Sao_Paulo'
        self.assertTrue(any('unqualified local time' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_duplicate_event_id_rejected(self):
        rec,old=case('pl-1777-2025')
        rec['events'][1]['event_id']=rec['events'][0]['event_id']
        self.assertTrue(any('duplicated events.event_id' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_no_automatic_impact_claims(self):
        rec,old=case('fundepar-pao-de-queijo-vegano')
        rec['editorial_assessment']['impact_claims']=['Student food delivery confirmed']
        self.assertTrue(any('impact claims' in x for x in validate_record(rec,old,slug=old['id'])))

    def test_last_observation_is_not_publication_date(self):
        rec,old=case('pl-1777-2025')
        rec['last_observed_at']='2025-04-22'
        self.assertTrue(any('last_observed_at differs' in x for x in validate_record(rec,old,slug=old['id'])))


if __name__=='__main__':unittest.main(verbosity=2)
