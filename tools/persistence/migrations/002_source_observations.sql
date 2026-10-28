-- Forward-only source history. No dossier/editorial/publication writes are implied.
-- Only selected Câmara fields and bounded provenance belong in these tables.

CREATE FUNCTION ndq.source_fields_valid(section text, value jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE allowed text[]; key text; nested jsonb;
BEGIN
    IF jsonb_typeof(value) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    CASE section
        WHEN 'proposition' THEN allowed := ARRAY['id','uri','siglaTipo','codTipo','numero','ano',
            'descricaoTipo','ementa','dataApresentacao','uriOrgaoNumerador','urnFinal','statusProposicao'];
        WHEN 'events' THEN allowed := ARRAY['dataHora','sequencia','siglaOrgao','uriOrgao',
            'uriUltimoRelator','regime','descricaoTramitacao','codTipoTramitacao',
            'descricaoSituacao','codSituacao','despacho','url','ambito','apreciacao'];
        WHEN 'authors' THEN allowed := ARRAY['uri','nome','codTipo','tipo','ordemAssinatura','proponente'];
        WHEN 'relationships' THEN allowed := ARRAY['id','uri','siglaTipo','codTipo','numero','ano','ementa'];
        ELSE RETURN false;
    END CASE;
    FOR key IN SELECT jsonb_object_keys(value) LOOP
        IF NOT key = ANY(allowed) THEN RETURN false; END IF;
    END LOOP;
    IF section = 'proposition' AND value ? 'statusProposicao' THEN
        nested := value -> 'statusProposicao';
        IF nested <> 'null'::jsonb THEN
            IF jsonb_typeof(nested) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
            allowed := ARRAY['dataHora','sequencia','siglaOrgao','uriOrgao','uriUltimoRelator',
                'regime','descricaoTramitacao','codTipoTramitacao','descricaoSituacao',
                'codSituacao','despacho','url','ambito','apreciacao'];
            FOR key IN SELECT jsonb_object_keys(nested) LOOP
                IF NOT key = ANY(allowed) THEN RETURN false; END IF;
            END LOOP;
        END IF;
    END IF;
    RETURN true;
END $$;

CREATE FUNCTION ndq.source_coverage_valid(value jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE section text; part jsonb;
BEGIN
    IF jsonb_typeof(value) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(value)) <> 4 THEN RETURN false; END IF;
    FOREACH section IN ARRAY ARRAY['proposition','events','authors','relationships'] LOOP
        part := value -> section;
        IF jsonb_typeof(part) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(part)) <> 7 OR
           jsonb_typeof(part -> 'complete') IS DISTINCT FROM 'boolean' OR
           jsonb_typeof(part -> 'returned_count') IS DISTINCT FROM 'number' OR
           jsonb_typeof(part -> 'pages') IS DISTINCT FROM 'array' OR
           jsonb_typeof(part -> 'issues') IS DISTINCT FROM 'array' OR
           jsonb_typeof(part -> 'normalization_issues') IS DISTINCT FROM 'array' OR
           jsonb_typeof(part -> 'field_policy') IS DISTINCT FROM 'object' THEN
            RETURN false;
        END IF;
        IF (part ->> 'returned_count')::numeric < 0 OR
           (part ->> 'returned_count')::numeric > 10000 OR
           jsonb_array_length(part -> 'pages') > 100 OR
           jsonb_array_length(part -> 'issues') > 100 OR
           jsonb_array_length(part -> 'normalization_issues') > 100 OR
           part ->> 'scope' IS DISTINCT FROM
             'returned_endpoint_pages_not_exhaustive_institutional_history' THEN
            RETURN false;
        END IF;
        IF (part ->> 'complete')::boolean AND jsonb_array_length(part -> 'pages') = 0 THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END $$;

CREATE TABLE ndq.source_snapshots (
    snapshot_id uuid PRIMARY KEY,
    synthetic_fixture boolean NOT NULL,
    institution text NOT NULL CHECK (institution = 'camara'),
    namespace text NOT NULL CHECK (namespace = 'proposicoes'),
    official_id text NOT NULL CHECK (official_id ~ '^[1-9][0-9]*$'),
    projection_version text NOT NULL CHECK (projection_version = 'camara-selected-fields-v1'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    first_observed_at timestamptz NOT NULL,
    coverage jsonb NOT NULL CHECK (ndq.source_coverage_valid(coverage)),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (institution, namespace, official_id, projection_version, content_hash),
    UNIQUE (snapshot_id, institution, namespace, official_id, projection_version),
    CHECK ((coverage -> 'proposition' ->> 'complete')::boolean AND
           (coverage -> 'events' ->> 'complete')::boolean AND
           (coverage -> 'authors' ->> 'complete')::boolean AND
           (coverage -> 'relationships' ->> 'complete')::boolean)
);

CREATE TABLE ndq.source_snapshot_items (
    snapshot_id uuid NOT NULL REFERENCES ndq.source_snapshots,
    endpoint_category text NOT NULL CHECK (endpoint_category IN
        ('proposition','events','authors','relationships')),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    match_key text,
    locator jsonb NOT NULL CHECK (jsonb_typeof(locator) = 'object'),
    selected_fields jsonb NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    evidence_url ndq.https_url NOT NULL,
    source_date text CHECK (source_date IS NULL OR length(source_date) <= 16000),
    source_date_semantic text NOT NULL CHECK (source_date_semantic IN
        ('presentation_date','procedural_source_date','not_supplied')),
    source_identity_uri ndq.https_url,
    PRIMARY KEY (snapshot_id, endpoint_category, ordinal),
    CHECK (ndq.source_fields_valid(endpoint_category, selected_fields)),
    CHECK (length(selected_fields::text) <= 100000),
    CHECK (endpoint_category = 'authors' OR source_identity_uri IS NULL),
    CHECK (endpoint_category <> 'proposition' OR ordinal = 0)
);
CREATE INDEX source_snapshot_items_match ON ndq.source_snapshot_items
    (snapshot_id, endpoint_category, match_key);

-- The sequence is allocated after the per-entity transaction lock. It orders
-- complete baselines without relying on wall-clock timestamps or UUID ordering.
CREATE TABLE ndq.source_collection_attempts (
    attempt_id uuid PRIMARY KEY,
    attempt_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    synthetic_fixture boolean NOT NULL,
    institution text NOT NULL CHECK (institution = 'camara'),
    namespace text NOT NULL CHECK (namespace = 'proposicoes'),
    official_id text NOT NULL CHECK (official_id ~ '^[1-9][0-9]*$'),
    projection_version text NOT NULL CHECK (projection_version = 'camara-selected-fields-v1'),
    completed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    outcome text NOT NULL CHECK (outcome IN ('complete','incomplete','failed')),
    transition_kind text NOT NULL CHECK (transition_kind IN
        ('initial','unchanged','changed','returned','incomplete','failed')),
    coverage jsonb NOT NULL CHECK (ndq.source_coverage_valid(coverage)),
    previous_snapshot_id uuid,
    snapshot_id uuid,
    UNIQUE (attempt_id, institution, namespace, official_id, projection_version),
    UNIQUE (attempt_id, previous_snapshot_id),
    UNIQUE (attempt_id, snapshot_id),
    FOREIGN KEY (snapshot_id, institution, namespace, official_id, projection_version)
        REFERENCES ndq.source_snapshots
        (snapshot_id, institution, namespace, official_id, projection_version),
    FOREIGN KEY (previous_snapshot_id, institution, namespace, official_id, projection_version)
        REFERENCES ndq.source_snapshots
        (snapshot_id, institution, namespace, official_id, projection_version),
    CHECK ((outcome = 'complete' AND snapshot_id IS NOT NULL AND
            transition_kind IN ('initial','unchanged','changed','returned')) OR
           (outcome <> 'complete' AND snapshot_id IS NULL AND
            transition_kind = outcome)),
    CHECK ((transition_kind = 'initial' AND previous_snapshot_id IS NULL) OR
           (transition_kind <> 'initial')),
    CHECK ((transition_kind = 'unchanged' AND previous_snapshot_id IS NOT NULL AND
            snapshot_id = previous_snapshot_id) OR
           transition_kind <> 'unchanged'),
    CHECK ((transition_kind IN ('changed','returned') AND
            previous_snapshot_id IS NOT NULL AND snapshot_id <> previous_snapshot_id) OR
           transition_kind NOT IN ('changed','returned'))
);
CREATE INDEX source_attempts_latest_complete ON ndq.source_collection_attempts
    (institution, namespace, official_id, projection_version, attempt_sequence DESC)
    WHERE outcome = 'complete';

-- Selected rows from an incomplete attempt remain evidence, but cannot become
-- an authoritative snapshot or silently replace the last complete baseline.
CREATE TABLE ndq.source_attempt_items (
    attempt_id uuid NOT NULL REFERENCES ndq.source_collection_attempts,
    endpoint_category text NOT NULL CHECK (endpoint_category IN
        ('proposition','events','authors','relationships')),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    match_key text,
    locator jsonb NOT NULL CHECK (jsonb_typeof(locator) = 'object'),
    selected_fields jsonb NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    evidence_url ndq.https_url NOT NULL,
    source_date text CHECK (source_date IS NULL OR length(source_date) <= 16000),
    source_date_semantic text NOT NULL CHECK (source_date_semantic IN
        ('presentation_date','procedural_source_date','not_supplied')),
    source_identity_uri ndq.https_url,
    PRIMARY KEY (attempt_id, endpoint_category, ordinal),
    CHECK (ndq.source_fields_valid(endpoint_category, selected_fields)),
    CHECK (length(selected_fields::text) <= 100000),
    CHECK (endpoint_category = 'authors' OR source_identity_uri IS NULL),
    CHECK (endpoint_category <> 'proposition' OR ordinal = 0)
);

-- Candidates describe a comparison of two immutable official snapshots. They
-- are not procedural events, editorial proposals, approvals or publications.
CREATE TABLE ndq.source_change_candidates (
    candidate_id uuid PRIMARY KEY,
    candidate_key text NOT NULL UNIQUE CHECK (candidate_key ~ '^[0-9a-f]{64}$'),
    institution text NOT NULL CHECK (institution = 'camara'),
    namespace text NOT NULL CHECK (namespace = 'proposicoes'),
    official_id text NOT NULL CHECK (official_id ~ '^[1-9][0-9]*$'),
    projection_version text NOT NULL CHECK (projection_version = 'camara-selected-fields-v1'),
    first_attempt_id uuid NOT NULL,
    before_snapshot_id uuid NOT NULL,
    after_snapshot_id uuid NOT NULL,
    section text NOT NULL CHECK (section IN ('proposition','events','authors','relationships')),
    item_key text,
    field_path text,
    classification text NOT NULL CHECK (classification IN ('new','changed','conflicting','uncertain')),
    reason text NOT NULL CHECK (reason <> ''),
    before_value jsonb,
    after_value jsonb,
    evidence_url ndq.https_url NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (before_snapshot_id <> after_snapshot_id),
    CHECK (before_value IS NULL OR length(before_value::text) <= 100000),
    CHECK (after_value IS NULL OR length(after_value::text) <= 100000),
    FOREIGN KEY (first_attempt_id, institution, namespace, official_id, projection_version)
        REFERENCES ndq.source_collection_attempts
        (attempt_id, institution, namespace, official_id, projection_version),
    FOREIGN KEY (first_attempt_id, before_snapshot_id)
        REFERENCES ndq.source_collection_attempts (attempt_id, previous_snapshot_id),
    FOREIGN KEY (first_attempt_id, after_snapshot_id)
        REFERENCES ndq.source_collection_attempts (attempt_id, snapshot_id),
    FOREIGN KEY (before_snapshot_id, institution, namespace, official_id, projection_version)
        REFERENCES ndq.source_snapshots
        (snapshot_id, institution, namespace, official_id, projection_version),
    FOREIGN KEY (after_snapshot_id, institution, namespace, official_id, projection_version)
        REFERENCES ndq.source_snapshots
        (snapshot_id, institution, namespace, official_id, projection_version)
);

-- These tables are explicit future workflow surfaces. The source writer never
-- inserts here, and they do not implicitly create dossier revisions.
CREATE TABLE ndq.source_editorial_proposals (
    proposal_id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES ndq.source_change_candidates,
    proposal_text text NOT NULL CHECK (btrim(proposal_text) <> ''),
    proposed_by text NOT NULL CHECK (btrim(proposed_by) <> ''),
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ndq.source_editorial_decisions (
    decision_id uuid PRIMARY KEY,
    proposal_id uuid NOT NULL REFERENCES ndq.source_editorial_proposals,
    decision text NOT NULL CHECK (decision IN ('approved_private','changes_requested','rejected')),
    reviewer text NOT NULL CHECK (btrim(reviewer) <> ''),
    rationale text NOT NULL CHECK (btrim(rationale) <> ''),
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Migration 001's trigger creation covered only tables present at that time.
DO $$ DECLARE table_name text; BEGIN
    FOREACH table_name IN ARRAY ARRAY['source_snapshots','source_snapshot_items',
        'source_collection_attempts','source_attempt_items','source_change_candidates',
        'source_editorial_proposals','source_editorial_decisions'] LOOP
        EXECUTE format('CREATE TRIGGER preserve_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ndq.%I FOR EACH STATEMENT EXECUTE FUNCTION ndq.reject_mutation()', table_name);
    END LOOP;
END $$;
