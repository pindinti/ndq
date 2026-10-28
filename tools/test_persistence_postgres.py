"""Opt-in tests on an EMPTY disposable LOOPBACK database named ndq_test.

Nothing connects unless NDQ_TEST_DATABASE_URL is explicitly set. All schema and
data writes live inside an outer rollback-only transaction; no DROP or DELETE.
Misconfiguration, connection errors and SQL failures fail rather than skip.
"""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from tools.persistence.contract import Pilot, digest, item_key, load_pilots
from tools.persistence.db import MIGRATIONS, connect, migrate
from tools.persistence.repository import IdentityConflict, import_pilots, read_record, schedule_check
from tools.persistence.source_observations import SourceObservationError, write_local_test_observation

TEST_URL = os.environ.get('NDQ_TEST_DATABASE_URL', '')


@unittest.skipUnless(TEST_URL, 'PostgreSQL integration: set NDQ_TEST_DATABASE_URL to an empty local ndq_test database')
class PostgresPersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        from psycopg import Error
        config = conninfo_to_dict(TEST_URL)
        if config.get('host') not in ('localhost', '127.0.0.1', '::1') or config.get('dbname') != 'ndq_test':
            raise ValueError('Integration tests require explicit loopback host and database name ndq_test')
        if any(k in config for k in ('hostaddr', 'service')):
            raise ValueError('Integration tests forbid hostaddr/service overrides')
        cls.sql_error = Error
        cls.conn = connect(TEST_URL)
        cls.addClassCleanup(cls.conn.close)
        if cls.conn.execute("SELECT to_regnamespace('ndq'), to_regclass('public.ndq_schema_migrations')").fetchone() != (None, None):
            raise ValueError('Integration tests require an empty disposable database; existing NDQ schema refused')
        cls.outer = cls.conn.transaction(force_rollback=True)
        cls.outer.__enter__()
        cls.addClassCleanup(cls.outer.__exit__, None, None, None)
        cls.first_migration = migrate(cls.conn)
        cls.pilots = load_pilots()

    def setUp(self):
        self.tx = self.conn.transaction(force_rollback=True)
        self.tx.__enter__()
        self.addCleanup(self.tx.__exit__, None, None, None)

    def sql_rejected(self, query, params=()):
        with self.assertRaises(self.sql_error):
            with self.conn.transaction():
                self.conn.execute(query, params)

    def seed(self):
        return import_pilots(self.conn, self.pilots)

    def revision(self, slug='pl-1777-2025'):
        return self.conn.execute('SELECT revision_id FROM ndq.latest_revisions WHERE slug = %s', (slug,)).fetchone()[0]

    def observation(self, revision, key, *, category='relationship', source='camara-proposicao', position=100):
        self.conn.execute('''INSERT INTO ndq.observations
            (revision_id, observation_key, category, position, primary_source_id, data)
            VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)''', (revision, key, category, position, source))

    def test_migrations_idempotent_and_checksum_protected(self):
        self.assertEqual(['001_foundation.sql', '002_source_observations.sql'], self.first_migration)
        self.assertEqual([], migrate(self.conn))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '001_foundation.sql'
            path.write_bytes((MIGRATIONS / path.name).read_bytes() + b'\n-- tampered\n')
            second = Path(tmp) / '002_source_observations.sql'
            second.write_bytes((MIGRATIONS / second.name).read_bytes())
            with self.assertRaisesRegex(ValueError, 'modified'):
                migrate(self.conn, Path(tmp))

    def source(self, data=None, *, complete=True, pid=42):
        from tools.collectors.http import BASE, FetchResult
        from tools.collectors.camara.normalize import normalize
        from tools.test_camara_collector import fixture_data
        data = copy.deepcopy(data if data is not None else fixture_data())
        suffixes = {'proposition': '', 'events': '/tramitacoes',
                    'authors': '/autores', 'relationships': '/relacionadas'}
        fetched = {name: FetchResult(f'{BASE}/proposicoes/{pid}{suffix}', data[name], complete,
                                  [{'url': f'{BASE}/proposicoes/{pid}{suffix}',
                                    'retrieved_at': '2026-10-27T14:00:00Z'}])
                   for name, suffix in suffixes.items()}
        return normalize(pid, fetched, '2026-10-27T14:00:00Z')

    def write_source(self, source):
        return write_local_test_observation(self.conn, source, allow_local_test=True)

    def test_source_initial_identical_changed_and_returned(self):
        from tools.test_camara_collector import fixture_data
        first = self.source()
        a = self.write_source(first)
        repeated = self.write_source(first)
        changed_data = fixture_data()
        changed_data['events'][0]['despacho'] = 'Synthetic corrected literal despacho'
        b = self.write_source(self.source(changed_data))
        returned = self.write_source(first)
        self.assertEqual(['initial', 'unchanged', 'changed', 'returned'],
                         [x['transition'] for x in (a, repeated, b, returned)])
        self.assertEqual([True, False, True, False],
                         [x['snapshot_created'] for x in (a, repeated, b, returned)])
        self.assertEqual((2, 4), self.conn.execute('''SELECT
            (SELECT count(*) FROM ndq.source_snapshots),
            (SELECT count(*) FROM ndq.source_collection_attempts)''').fetchone())
        self.assertEqual(2, self.conn.execute('SELECT count(*) FROM ndq.source_change_candidates').fetchone()[0])
        self.write_source(self.source(changed_data))
        self.assertEqual(2, self.conn.execute('SELECT count(*) FROM ndq.source_change_candidates').fetchone()[0])
        self.assertEqual((0, 0, 0, 0), self.conn.execute('''SELECT
            (SELECT count(*) FROM ndq.dossier_revisions),
            (SELECT count(*) FROM ndq.publication_states),
            (SELECT count(*) FROM ndq.source_editorial_proposals),
            (SELECT count(*) FROM ndq.source_editorial_decisions)''').fetchone())

    def test_source_partial_failure_and_recovery_keep_valid_baseline(self):
        from tools.collectors.http import BASE, FetchResult
        from tools.collectors.camara.normalize import normalize
        partial = self.write_source(self.source(complete=False))
        self.assertEqual(('incomplete', 0), (partial['transition'],
            self.conn.execute('SELECT count(*) FROM ndq.source_snapshots').fetchone()[0]))
        complete = self.write_source(self.source())
        self.assertEqual('initial', complete['transition'])
        self.write_source(self.source(complete=False))
        recovered = self.write_source(self.source())
        self.assertEqual('unchanged', recovered['transition'])
        fetched = {name: FetchResult(f'{BASE}/proposicoes/42{suffix}',
                   None if name == 'proposition' else [], False)
                   for name, suffix in (('proposition',''), ('events','/tramitacoes'),
                                        ('authors','/autores'), ('relationships','/relacionadas'))}
        failed = self.write_source(normalize(42, fetched, '2026-10-27T14:00:00Z'))
        self.assertEqual('failed', failed['outcome'])
        self.assertEqual(1, self.conn.execute('SELECT count(*) FROM ndq.source_snapshots').fetchone()[0])
        self.assertEqual(0, self.conn.execute('SELECT count(*) FROM ndq.source_change_candidates').fetchone()[0])

    def test_missing_event_match_key_is_preserved_only_as_attempt_evidence(self):
        from tools.test_camara_collector import fixture_data
        data = fixture_data()
        del data['events'][0]['sequencia']
        source = self.source(data)
        self.assertFalse(source['coverage']['events']['complete'])
        self.assertIsNone(source['events'][0]['match_key'])
        result = self.write_source(source)
        self.assertEqual('incomplete', result['outcome'])
        self.assertEqual(0, self.conn.execute('SELECT count(*) FROM ndq.source_snapshots').fetchone()[0])
        row = self.conn.execute('''SELECT match_key, selected_fields ->> 'despacho', evidence_url
            FROM ndq.source_attempt_items WHERE endpoint_category = 'events' ''').fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(data['events'][0]['despacho'], row[1])
        self.assertTrue(row[2].endswith('/tramitacoes'))

    def test_source_new_event_ambiguous_date_agency_and_identity_collision(self):
        from tools.test_camara_collector import fixture_data
        self.write_source(self.source())
        data = fixture_data()
        data['events'].append(dict(data['events'][0], sequencia=2))
        result = self.write_source(self.source(data))
        self.assertEqual('changed', result['transition'])
        self.assertEqual('new', self.conn.execute('''SELECT classification FROM ndq.source_change_candidates
            WHERE item_key LIKE 'camara-compound-v1:%' AND classification = 'new' LIMIT 1''').fetchone()[0])
        data['events'][0]['dataHora'] = '2025-01-03T00:00'
        data['events'][0]['siglaOrgao'] = 'OTHER'
        result = self.write_source(self.source(data))
        self.assertGreater(result['new_change_candidates'], 0)
        self.assertGreater(self.conn.execute('''SELECT count(*) FROM ndq.source_change_candidates
            WHERE classification = 'uncertain' AND reason = 'possible_event_date_or_agency_correction' ''').fetchone()[0], 0)
        data['events'].append(dict(data['events'][0], despacho='Conflicting same compound key'))
        self.write_source(self.source(data))
        self.assertEqual(2, self.conn.execute('''SELECT count(*) FROM ndq.source_snapshot_items
            WHERE snapshot_id = (SELECT snapshot_id FROM ndq.source_collection_attempts
                ORDER BY attempt_sequence DESC LIMIT 1) AND endpoint_category = 'events'
              AND match_key = (SELECT match_key FROM ndq.source_snapshot_items
                WHERE snapshot_id = (SELECT snapshot_id FROM ndq.source_collection_attempts
                    ORDER BY attempt_sequence DESC LIMIT 1)
                  AND endpoint_category = 'events' AND selected_fields ->> 'siglaOrgao' = 'OTHER' LIMIT 1)''').fetchone()[0])

    def test_source_append_only_fk_and_transaction_rollback(self):
        initial = self.write_source(self.source())
        self.assertTrue(initial['snapshot_created'])
        self.sql_rejected('UPDATE ndq.source_snapshots SET content_hash = content_hash')
        self.sql_rejected('DELETE FROM ndq.source_collection_attempts')
        self.sql_rejected('UPDATE ndq.source_attempt_items SET ordinal = ordinal')
        self.sql_rejected('TRUNCATE ndq.source_snapshot_items')
        self.sql_rejected('''INSERT INTO ndq.source_snapshot_items
            (snapshot_id, endpoint_category, ordinal, locator, selected_fields,
             fingerprint, evidence_url, source_date_semantic)
            VALUES (%s,'authors',0,'{}'::jsonb,'{}'::jsonb,%s,%s,'not_supplied')''',
            (uuid4(), '0'*64, 'https://dadosabertos.camara.leg.br/api/v2/proposicoes/42/autores'))
        before = self.conn.execute('SELECT count(*) FROM ndq.source_snapshots').fetchone()[0]
        class FailingProxy:
            def __init__(self, conn): self.real, self.info = conn, conn.info
            def transaction(self): return self.real.transaction()
            def execute(self, query, params=()):
                if 'INSERT INTO ndq.source_snapshot_items' in query:
                    raise RuntimeError('password=must-not-appear')
                return self.real.execute(query, params)
        from tools.test_camara_collector import fixture_data
        changed = fixture_data()
        changed['events'][0]['despacho'] = 'Rollback fixture'
        with self.assertRaises(SourceObservationError) as caught:
            write_local_test_observation(FailingProxy(self.conn), self.source(changed), allow_local_test=True)
        self.assertNotIn('must-not-appear', str(caught.exception))
        self.assertEqual(before, self.conn.execute('SELECT count(*) FROM ndq.source_snapshots').fetchone()[0])

    def test_source_cross_identity_candidate_fk_is_rejected(self):
        from tools.test_camara_collector import fixture_data
        a = self.write_source(self.source())
        other = fixture_data()
        other['proposition']['id'] = 43
        other['proposition']['uri'] = 'https://dadosabertos.camara.leg.br/api/v2/proposicoes/43'
        self.write_source(self.source(other, pid=43))
        ids = self.conn.execute('''SELECT official_id, snapshot_id FROM ndq.source_snapshots
            ORDER BY official_id''').fetchall()
        attempt_id = self.conn.execute('''SELECT attempt_id FROM ndq.source_collection_attempts
            WHERE official_id = '42' LIMIT 1''').fetchone()[0]
        self.assertEqual(['42', '43'], [x[0] for x in ids])
        self.sql_rejected('''INSERT INTO ndq.source_change_candidates
            (candidate_id,candidate_key,institution,namespace,official_id,projection_version,
             first_attempt_id,before_snapshot_id,after_snapshot_id,section,classification,
             reason,evidence_url)
            VALUES (%s,%s,'camara','proposicoes','42','camara-selected-fields-v1',
                    %s,%s,%s,'events','uncertain','cross_identity_test',%s)''',
            (uuid4(), '1'*64, attempt_id, ids[0][1], ids[1][1],
             'https://dadosabertos.camara.leg.br/api/v2/proposicoes/42/tramitacoes'))

    def test_import_roundtrip_and_exact_idempotency(self):
        first = self.seed()
        self.assertEqual(3, len(first['imported']))
        second = self.seed()
        self.assertEqual([], second['imported'])
        self.assertEqual(3, len(second['unchanged']))
        self.assertIsNone(second['run_id'])
        for p in self.pilots:
            rev = self.revision(p.record['slug'])
            self.assertEqual(p.record, read_record(self.conn, rev))
        self.assertEqual((3, 3, 9, 4, 1), self.conn.execute('''SELECT
            (SELECT count(*) FROM ndq.dossiers), (SELECT count(*) FROM ndq.dossier_revisions),
            (SELECT count(*) FROM ndq.events), (SELECT count(*) FROM ndq.official_identities),
            (SELECT count(*) FROM ndq.collection_runs)''').fetchone())
        # Exercise deferred run FKs within the test transaction, before rollback.
        self.conn.execute('SET CONSTRAINTS ALL IMMEDIATE')

    def test_changed_content_and_reversion_keep_all_revisions(self):
        self.seed()
        pilot = next(p for p in self.pilots if p.record['slug'] == 'pl-1777-2025')
        old_rev = self.revision()
        changed = copy.deepcopy(pilot.record)
        changed['events'][0]['description'] = 'Correction documented in local test input'
        report = import_pilots(self.conn, [Pilot(changed, digest(changed))])
        self.assertEqual(2, report['imported'][0]['revision_number'])
        self.assertEqual(pilot.record, read_record(self.conn, old_rev))
        self.assertEqual(changed, read_record(self.conn, self.revision()))
        report = import_pilots(self.conn, [pilot])
        self.assertEqual(3, report['imported'][0]['revision_number'])
        self.assertEqual(21, self.conn.execute('''SELECT count(*) FROM ndq.events e JOIN ndq.dossier_revisions r
            USING (revision_id) WHERE r.slug = 'pl-1777-2025' ''').fetchone()[0])

    def test_append_only_including_truncate(self):
        self.seed()
        self.sql_rejected("UPDATE ndq.facts SET verification_status = 'invented'")
        self.sql_rejected('DELETE FROM ndq.events')
        self.sql_rejected('TRUNCATE ndq.check_results')

    def test_identity_conflict_rolls_back_whole_batch(self):
        self.seed()
        pilot = next(p for p in self.pilots if p.record['slug'] == 'pl-1777-2025')
        changed = copy.deepcopy(pilot.record)
        changed['events'][0]['description'] = 'First write must roll back with the later conflict'
        agenda = next(p for p in self.pilots if p.record['slug'] == 'anvisa-plant-based')
        altered = copy.deepcopy(agenda.record)
        conflicting = copy.deepcopy(pilot.record['official_identities'][0])
        conflicting['source_ids'] = ['anvisa-planilha']
        altered['official_identities'] = [conflicting]
        with self.assertRaises(IdentityConflict):
            import_pilots(self.conn, [Pilot(changed, digest(changed)), Pilot(altered, digest(altered))])
        self.assertEqual(3, self.conn.execute('SELECT count(*) FROM ndq.dossiers').fetchone()[0])
        self.assertEqual(3, self.conn.execute('SELECT count(*) FROM ndq.dossier_revisions').fetchone()[0])
        self.assertEqual(1, self.conn.execute('SELECT count(*) FROM ndq.collection_runs').fetchone()[0])

    def test_slug_collision_never_reassigns_dossier_identity(self):
        self.seed()
        pilot = next(p for p in self.pilots if p.record['slug'] == 'pl-1777-2025')
        altered = copy.deepcopy(pilot.record)
        altered['dossier_id'] = 'ndq:test:distinct-internal-id'
        with self.assertRaisesRegex(IdentityConflict, 'Slug'):
            import_pilots(self.conn, [Pilot(altered, digest(altered))])

    def test_missing_observation_preserves_history_without_negative_fact(self):
        self.seed()
        pilot = next(p for p in self.pilots if p.record['slug'] == 'pl-1777-2025')
        old_rev = self.revision()
        altered = copy.deepcopy(pilot.record)
        altered['facts'] = altered['facts'][:1]
        import_pilots(self.conn, [Pilot(altered, digest(altered))])
        self.assertEqual(2, len(read_record(self.conn, old_rev)['facts']))
        self.assertEqual(altered, read_record(self.conn, self.revision()))

    def test_identity_cannot_attach_to_another_dossier_via_sql(self):
        self.seed()
        rev = self.revision('anvisa-plant-based')
        self.observation(rev, 'test-identity', category='identity', source='anvisa-planilha')
        self.sql_rejected('''INSERT INTO ndq.identity_observations
            (revision_id, observation_key, institution, namespace, value, reference_url, verified_at)
            VALUES (%s, 'test-identity', 'camara', 'proposicoes', '2498778', 'https://example.org/act', '2026-10-27')''', (rev,))
        self.sql_rejected('''INSERT INTO ndq.official_identities
            (institution, namespace, value, dossier_id, first_revision_id, first_observation_key)
            SELECT institution, namespace, value, dossier_id, first_revision_id, first_observation_key
            FROM ndq.official_identities LIMIT 1''')

    def test_multiple_documented_identifiers_share_one_dossier(self):
        self.seed()
        self.assertEqual(2, self.conn.execute('''SELECT count(*) FROM ndq.official_identities
            WHERE dossier_id = 'ndq:pncp:22112109000153:2026:82' ''').fetchone()[0])

    def test_event_dates_and_context_are_distinct(self):
        self.seed()
        self.assertEqual(('2025-04-22T15:50', None, '2026-10-27', 'not_provided_by_source'),
            self.conn.execute('''SELECT occurred_at, published_at, observed_at, timezone_status FROM ndq.events
            WHERE occurred_at = '2025-04-22T15:50' ''').fetchone())
        self.assertEqual(0, self.conn.execute('''SELECT count(*) FROM ndq.events e JOIN ndq.dossier_revisions r
            USING (revision_id) WHERE r.slug = 'anvisa-plant-based' ''').fetchone()[0])
        self.assertEqual(('month', '2021-10'), self.conn.execute('''SELECT date_precision, period
            FROM ndq.contextual_milestones WHERE period = '2021-10' ''').fetchone())
        self.assertEqual('2026-06-11T08:30', self.conn.execute('''SELECT scheduled_for FROM ndq.contextual_milestones
            WHERE context_kind = 'scheduled_date_not_confirmed_occurrence' ''').fetchone()[0])

    def test_database_rejects_future_or_invalid_occurrences(self):
        self.seed()
        rev = self.revision()
        self.observation(rev, 'future', category='event')
        query = '''INSERT INTO ndq.events (revision_id, observation_key, event_type, event_state,
            occurred_at, observed_at, timezone_status) VALUES (%s, 'future', 'test', 'occurred', %s, '2026-10-27', %s)'''
        for occurred, zone in (('2027-01-01', 'date_only'), ('2026-02-30', 'date_only'),
                               ('2026-10-26T08:00', 'date_only')):
            with self.subTest(occurred=occurred):
                self.sql_rejected(query, (rev, occurred, zone))

    def test_scheduled_event_cannot_have_occurrence(self):
        self.seed()
        rev = self.revision()
        self.observation(rev, 'schedule', category='event')
        self.sql_rejected('''INSERT INTO ndq.events (revision_id, observation_key, event_type, event_state,
            occurred_at, scheduled_for, observed_at, timezone_status) VALUES
            (%s, 'schedule', 'test', 'scheduled', '2026-10-26', '2026-10-28', '2026-10-27', 'date_only')''', (rev,))

    def test_context_cannot_be_inserted_as_formal_event(self):
        self.seed()
        rev = self.revision('anvisa-plant-based')
        item = next(p.record['contextual_milestones'][0] for p in self.pilots if p.record['slug'] == 'anvisa-plant-based')
        key = item_key('contextual_milestones', item)
        self.sql_rejected('''INSERT INTO ndq.events (revision_id, observation_key, event_type, event_state,
            occurred_at, observed_at, timezone_status) VALUES (%s, %s, 'invented', 'occurred', '2021-10-01', '2026-10-27', 'date_only')''', (rev, key))

    def test_provenance_cannot_reference_other_revision(self):
        self.seed()
        rev = self.revision('anvisa-plant-based')
        self.sql_rejected('''INSERT INTO ndq.observations
            (revision_id, observation_key, category, position, primary_source_id, data)
            VALUES (%s, 'cross', 'fact', 100, 'camara-proposicao', '{}')''', (rev,))

    def test_relationships_require_provenance_and_exactly_one_target(self):
        self.seed()
        rev = self.revision()
        self.sql_rejected('''INSERT INTO ndq.relationships
            (revision_id, observation_key, relation_type, origin, target_dossier_id, verification_status)
            VALUES (%s, 'no-evidence', 'references', 'official_document', 'ndq:anvisa:agenda:AR2627-3.7', 'documented')''', (rev,))
        self.observation(rev, 'relation')
        self.sql_rejected('''INSERT INTO ndq.relationships
            (revision_id, observation_key, relation_type, origin, verification_status)
            VALUES (%s, 'relation', 'references', 'editorial_proposal', 'not_verified')''', (rev,))
        self.conn.execute('''INSERT INTO ndq.relationships
            (revision_id, observation_key, relation_type, origin, target_dossier_id, verification_status)
            VALUES (%s, 'relation', 'references', 'official_document', 'ndq:anvisa:agenda:AR2627-3.7', 'documented_test_only')''', (rev,))
        self.assertEqual('camara-proposicao', self.conn.execute('''SELECT o.primary_source_id FROM ndq.relationships r
            JOIN ndq.observations o USING (revision_id, observation_key) WHERE r.observation_key = 'relation' ''').fetchone()[0])

    def test_relationship_can_target_an_official_act_without_a_dossier(self):
        self.seed()
        rev, act = self.revision(), uuid4()
        self.conn.execute('''INSERT INTO ndq.official_acts (act_id, institution, namespace, value, reference_url)
            VALUES (%s, 'test', 'test', 'local-example', 'https://example.org/act')''', (act,))
        self.observation(rev, 'act-relation')
        self.conn.execute('''INSERT INTO ndq.relationships
            (revision_id, observation_key, relation_type, origin, target_act_id, verification_status)
            VALUES (%s, 'act-relation', 'references', 'official_document', %s, 'test_only')''', (rev, act))

    def test_rectification_preserves_values_and_evidence(self):
        self.seed()
        self.assertEqual((885839000, 885775000), self.conn.execute('''SELECT previous_value, new_value
            FROM ndq.documented_changes''').fetchone())
        self.assertEqual(2, self.conn.execute('''SELECT count(*) FROM ndq.observation_evidence e
            JOIN ndq.documented_changes c USING (revision_id, observation_key)''').fetchone()[0])

    def test_editorial_review_separate_from_publication_and_verification(self):
        self.seed()
        self.assertEqual([('pending_review', None, 'private_preview_only')] * 3,
            self.conn.execute('''SELECT review_state, reviewer, state FROM ndq.editorial_reviews
                JOIN ndq.publication_states USING (revision_id)''').fetchall())
        # Separate type constraints reject public approval, even for new revisions.
        rev = self.revision()
        self.sql_rejected("INSERT INTO ndq.publication_states VALUES (%s, 'published')", (rev,))
        self.assertEqual('prototype_approved_for_private_integration', self.conn.execute('''SELECT
            origin_review_status FROM ndq.editorial_reviews LIMIT 1''').fetchone()[0])

    def test_scheduled_checks_idempotency_conflicts_and_results(self):
        self.seed()
        rev = self.revision()
        event = next(p.record['events'][0] for p in self.pilots if p.record['slug'] == 'pl-1777-2025')
        args = dict(revision_id=rev, basis_key=item_key('events', event),
                    due_at=datetime(2026, 10, 28, 12, tzinfo=timezone.utc), reason='Explicit test policy', idempotency_key='test-check')
        check = schedule_check(self.conn, **args)
        self.assertEqual(check, schedule_check(self.conn, **args))
        with self.assertRaises(ValueError):
            schedule_check(self.conn, **dict(args, reason='Different policy'))
        with self.assertRaises(self.sql_error):
            schedule_check(self.conn, **dict(args, basis_key='unknown', idempotency_key='missing-basis'))
        self.conn.execute('''INSERT INTO ndq.check_results (result_id, check_id, outcome, note)
            VALUES (%s, %s, 'source_unavailable', 'No negative conclusion follows from missing evidence')''', (uuid4(), check))
        self.assertEqual('source_unavailable', self.conn.execute('SELECT outcome FROM ndq.check_results').fetchone()[0])

    def test_invalid_input_does_not_start_a_run(self):
        pilot = self.pilots[0]
        invalid = copy.deepcopy(pilot.record)
        invalid['facts'][0]['source_ids'] = ['missing']
        with self.assertRaises(ValueError):
            import_pilots(self.conn, [Pilot(invalid, digest(invalid))])
        self.assertEqual(0, self.conn.execute('SELECT count(*) FROM ndq.collection_runs').fetchone()[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
