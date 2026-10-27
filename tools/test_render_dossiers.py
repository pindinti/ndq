#!/usr/bin/env python3
"""Regression and misuse tests for the NDQ v0.3 structured dossier renderer."""
from __future__ import annotations
import copy
import json
import unittest
from pathlib import Path
from render_dossiers import TEMPLATES, render_record, money_br
from validate_dossiers import RECORDS, LEGACY, validate_record


def data(slug):
    filename=slug+'.json'
    rec=json.loads((RECORDS/filename).read_text('utf-8'))
    tpl=json.loads((TEMPLATES/filename).read_text('utf-8'))
    return rec,tpl


class RenderDossiersTest(unittest.TestCase):
    def test_pl_seven_events_in_order(self):
        rec,tpl=data('pl-1777-2025')
        _,html=render_record(rec,tpl)
        self.assertEqual(7,html.count('data-ndq-kind="official-event"'))
        self.assertEqual(0,html.count('data-ndq-milestone='))
        self.assertLess(html.find('Apresentação de Proposição'),html.find('Encerramento de Prazo'))
        self.assertIn('fuso não especificado',html)

    def test_pl_change_to_record_reflects_in_html_not_template(self):
        rec,tpl=data('pl-1777-2025')
        rec['events'][0]['description']='Movimento com nova redação de teste.'
        _,out=render_record(rec,tpl)
        self.assertIn('Movimento com nova redação de teste.',out)
        self.assertNotIn('Movimento com nova redação de teste.',tpl['detail_html'])

    def test_anvisa_milestones_are_not_official_process_events(self):
        rec,tpl=data('anvisa-plant-based')
        _,out=render_record(rec,tpl)
        self.assertEqual(0,out.count('data-ndq-kind="official-event"'))
        self.assertEqual(5,out.count('data-ndq-milestone='))
        self.assertIn('data-ndq-kind="ndq_observation_not_agency_event"',out)
        self.assertIn('datetime="2021-10"',out)
        self.assertNotIn('datetime="2021-10-01"',out)

    def test_fundepar_has_two_official_documents_plus_two_contextual_entries(self):
        rec,tpl=data('fundepar-pao-de-queijo-vegano')
        _,out=render_record(rec,tpl)
        self.assertEqual(2,out.count('data-ndq-kind="official-event"'))
        self.assertEqual(2,out.count('data-ndq-milestone='))
        self.assertIn('data-ndq-kind="scheduled_date_not_confirmed_occurrence"',out)
        self.assertIn('data prevista',out.lower())
        self.assertIn('não confirma que a sessão ocorreu',out)

    def test_fundepar_change_amounts_generated(self):
        rec,tpl=data('fundepar-pao-de-queijo-vegano')
        _,out=render_record(rec,tpl)
        self.assertIn('R$ 8.858.390,00',out)
        self.assertIn('R$ 8.857.750,00',out)
        self.assertIn('Diferença: R$ 640,00 a menos',out)
        self.assertEqual('R$ 8.857.750,00',money_br(885775000))

    def test_fundepar_amount_mutation_propagates_if_consistent(self):
        rec,tpl=data('fundepar-pao-de-queijo-vegano')
        rec['type_data']['notice_versions'][-1]['maximum_value_brl_cents']=885774900
        rec['change_history'][0]['new_observed_value']=885774900
        rec['change_history'][0]['delta_brl_cents']=-64100
        _,out=render_record(rec,tpl)
        self.assertIn('R$ 8.857.749,00',out)
        self.assertIn('R$ 641,00 a menos',out)

    def test_fundepar_inconsistent_change_fails(self):
        rec,tpl=data('fundepar-pao-de-queijo-vegano')
        rec['change_history'][0]['delta_brl_cents']=-1
        with self.assertRaisesRegex(ValueError,'balance'):
            render_record(rec,tpl)

    def test_source_lists_are_exclusively_from_normalized_references(self):
        for slug in ['pl-1777-2025','anvisa-plant-based','fundepar-pao-de-queijo-vegano']:
            with self.subTest(slug=slug):
                rec,tpl=data(slug)
                _,out=render_record(rec,tpl)
                self.assertEqual(len(rec['source_references']),out.count('data-ndq-source='))
                for src in rec['source_references']:
                    self.assertIn('data-ndq-source="'+src['source_id']+'"',out)

    def test_data_is_html_escaped(self):
        rec,tpl=data('pl-1777-2025')
        rec['events'][0]['description']='<script>alert(1)</script>'
        rec['source_references'][0]['display_label']='<img src=x onerror=alert(2)>'
        _,out=render_record(rec,tpl)
        self.assertIn('&lt;script&gt;',out)
        self.assertNotIn('<script>alert(1)</script>',out)
        self.assertIn('&lt;img src=x onerror=alert(2)&gt;',out)

    def test_no_unknown_slot(self):
        rec,tpl=data('pl-1777-2025')
        tpl['detail_html']+='<p>{{NDQ_UNKNOWN}}</p>'
        with self.assertRaisesRegex(ValueError,'Unexpected marker'):
            render_record(rec,tpl)

    def test_no_duplicate_slot(self):
        rec,tpl=data('pl-1777-2025')
        tpl['detail_html']+='{{NDQ_TIMELINE}}'
        with self.assertRaisesRegex(ValueError,'Non-unique marker'):
            render_record(rec,tpl)

    def test_private_gate_remains_required(self):
        rec,tpl=data('anvisa-plant-based')
        rec['publication_status']='published'
        with self.assertRaisesRegex(ValueError,'Private editorial gate'):
            render_record(rec,tpl)

    def test_no_scheduled_milestone_promoted_to_occurrence(self):
        rec,tpl=data('fundepar-pao-de-queijo-vegano')
        rec['contextual_milestones'][-1]['occurred_at']='2026-06-11'
        old=json.loads((LEGACY/(rec['slug']+'.json')).read_text('utf-8'))
        errors=validate_record(rec,old,slug=rec['slug'])
        self.assertTrue(any('planned date cannot be treated' in x for x in errors))

    def test_no_month_to_day_fabrication(self):
        rec,tpl=data('anvisa-plant-based')
        rec['contextual_milestones'][0]['period']='2021-10-01'
        old=json.loads((LEGACY/(rec['slug']+'.json')).read_text('utf-8'))
        errors=validate_record(rec,old,slug=rec['slug'])
        self.assertTrue(any('month requires YYYY-MM' in x for x in errors))

    def test_context_must_have_source(self):
        rec,tpl=data('anvisa-plant-based')
        rec['contextual_milestones'][0]['source_ids']=['invalid-source']
        old=json.loads((LEGACY/(rec['slug']+'.json')).read_text('utf-8'))
        errors=validate_record(rec,old,slug=rec['slug'])
        self.assertTrue(any('unknown or absent source reference' in x for x in errors))

    def test_receipt_cannot_be_confused_with_goods_delivered(self):
        rec,tpl=data('fundepar-pao-de-queijo-vegano')
        rec['type_data']['goods_delivered']='YES'
        old=json.loads((LEGACY/(rec['slug']+'.json')).read_text('utf-8'))
        errors=validate_record(rec,old,slug=rec['slug'])
        self.assertTrue(any('contracted/delivered' in x for x in errors))


if __name__=='__main__': unittest.main(verbosity=2)
