-- Forward-only foundation; no extensions, downloads, raw documents or public views.
CREATE SCHEMA ndq;

-- Text preserves precision and unqualified source times without inventing a zone.
CREATE FUNCTION ndq.valid_temporal(v text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
    IF v !~ '^\d{4}-\d{2}-\d{2}(T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d{1,6})?)?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)?)?$' THEN
        RETURN false;
    END IF;
    PERFORM substring(v FROM 1 FOR 10)::date;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE DOMAIN ndq.source_time AS text CHECK (ndq.valid_temporal(VALUE));
CREATE DOMAIN ndq.https_url AS text CHECK (VALUE ~ '^https://[^/@[:space:]]+(/|$)' AND VALUE !~ '[[:space:]]');

CREATE TABLE ndq.source_systems (
    host text PRIMARY KEY CHECK (host <> ''),
    -- Derived URL host, not a claim about institutional ownership or coverage.
    registered_at timestamptz NOT NULL DEFAULT current_timestamp
);
CREATE TABLE ndq.collection_runs (
    run_id uuid PRIMARY KEY,
    origin text NOT NULL CHECK (origin IN ('fixture_import', 'official_collection')),
    started_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL CHECK (completed_at >= started_at),
    report jsonb NOT NULL CHECK (jsonb_typeof(report) = 'object')
);
CREATE TABLE ndq.run_sources (
    run_id uuid NOT NULL REFERENCES ndq.collection_runs DEFERRABLE INITIALLY DEFERRED,
    host text NOT NULL REFERENCES ndq.source_systems,
    PRIMARY KEY (run_id, host)
);
CREATE TABLE ndq.dossiers (
    dossier_id text PRIMARY KEY CHECK (dossier_id LIKE 'ndq:%'),
    kind text NOT NULL CHECK (kind IN ('legislative_proposal', 'regulatory_agenda_theme', 'public_procurement')),
    registered_at timestamptz NOT NULL DEFAULT current_timestamp
);
CREATE TABLE ndq.dossier_slugs (
    slug text PRIMARY KEY CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    dossier_id text NOT NULL REFERENCES ndq.dossiers,
    UNIQUE (slug, dossier_id)
);
CREATE TABLE ndq.dossier_revisions (
    revision_id uuid PRIMARY KEY,
    dossier_id text NOT NULL REFERENCES ndq.dossiers,
    revision_number integer NOT NULL CHECK (revision_number > 0),
    previous_revision_id uuid,
    run_id uuid NOT NULL REFERENCES ndq.collection_runs DEFERRABLE INITIALLY DEFERRED,
    slug text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    source_observed_at ndq.source_time NOT NULL,
    present_sections text[] NOT NULL,
    -- Curated provisional envelope: scope, presentation, type_data, quality, unknowns.
    -- No official response body or editorial HTML is accepted by the importer.
    metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT current_timestamp,
    UNIQUE (dossier_id, revision_number),
    UNIQUE (revision_id, dossier_id),
    FOREIGN KEY (slug, dossier_id) REFERENCES ndq.dossier_slugs (slug, dossier_id),
    FOREIGN KEY (previous_revision_id, dossier_id) REFERENCES ndq.dossier_revisions (revision_id, dossier_id),
    CHECK ((revision_number = 1) = (previous_revision_id IS NULL)),
    CHECK (previous_revision_id IS DISTINCT FROM revision_id)
);
CREATE INDEX dossier_revision_latest ON ndq.dossier_revisions (dossier_id, revision_number DESC);
CREATE TABLE ndq.document_references (
    revision_id uuid NOT NULL REFERENCES ndq.dossier_revisions,
    source_id text NOT NULL CHECK (source_id <> ''),
    position integer NOT NULL CHECK (position >= 0),
    host text NOT NULL REFERENCES ndq.source_systems,
    url ndq.https_url NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('institutional_html', 'institutional_json', 'institutional_xlsx', 'official_pdf', 'institutional_document_link')),
    observed_at ndq.source_time NOT NULL,
    access_state text NOT NULL CHECK (access_state IN ('documented_in_prior_review', 'observed_live', 'unavailable', 'not_checked')),
    metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata) = 'object'),
    PRIMARY KEY (revision_id, source_id),
    UNIQUE (revision_id, position)
);

-- Common provenance for each typed observation. The required first citation makes
-- evidence-free facts/relationships impossible, including via direct SQL writes.
CREATE TABLE ndq.observations (
    revision_id uuid NOT NULL REFERENCES ndq.dossier_revisions,
    observation_key text NOT NULL CHECK (observation_key <> ''),
    category text NOT NULL CHECK (category IN ('identity', 'fact', 'event', 'milestone', 'status', 'actor_role', 'relationship', 'change')),
    position integer NOT NULL CHECK (position >= 0),
    primary_source_id text NOT NULL,
    observed_at ndq.source_time,
    -- Only normalized, field-allowlisted observation data, not raw API responses.
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    PRIMARY KEY (revision_id, observation_key),
    UNIQUE (revision_id, observation_key, category),
    UNIQUE (revision_id, category, position),
    FOREIGN KEY (revision_id, primary_source_id) REFERENCES ndq.document_references,
    CHECK (category <> 'milestone' OR NOT (data ?| ARRAY['occurred_at', 'effective_at', 'legal_effect']))
);
CREATE TABLE ndq.observation_evidence (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    source_id text NOT NULL,
    citation_order integer NOT NULL CHECK (citation_order >= 0),
    PRIMARY KEY (revision_id, observation_key, source_id),
    UNIQUE (revision_id, observation_key, citation_order),
    FOREIGN KEY (revision_id, observation_key) REFERENCES ndq.observations,
    FOREIGN KEY (revision_id, source_id) REFERENCES ndq.document_references
);
CREATE TABLE ndq.official_identities (
    institution text NOT NULL CHECK (institution <> ''),
    namespace text NOT NULL CHECK (namespace <> ''),
    value text NOT NULL CHECK (value <> ''),
    dossier_id text NOT NULL REFERENCES ndq.dossiers,
    first_revision_id uuid NOT NULL,
    first_observation_key text NOT NULL,
    first_category text NOT NULL DEFAULT 'identity' CHECK (first_category = 'identity'),
    PRIMARY KEY (institution, namespace, value),
    FOREIGN KEY (first_revision_id, dossier_id) REFERENCES ndq.dossier_revisions (revision_id, dossier_id),
    FOREIGN KEY (first_revision_id, first_observation_key, first_category) REFERENCES ndq.observations (revision_id, observation_key, category)
);
CREATE TABLE ndq.identity_observations (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'identity' CHECK (category = 'identity'),
    institution text NOT NULL,
    namespace text NOT NULL,
    value text NOT NULL,
    reference_url ndq.https_url NOT NULL,
    verified_at ndq.source_time NOT NULL,
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category),
    FOREIGN KEY (institution, namespace, value) REFERENCES ndq.official_identities
);
CREATE TABLE ndq.facts (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'fact' CHECK (category = 'fact'),
    predicate text NOT NULL CHECK (predicate <> ''),
    value jsonb NOT NULL,
    verification_status text NOT NULL CHECK (verification_status <> ''),
    effective_at ndq.source_time,
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category)
);
CREATE TABLE ndq.events (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'event' CHECK (category = 'event'),
    event_type text NOT NULL CHECK (event_type <> ''),
    event_state text NOT NULL CHECK (event_state IN ('occurred', 'scheduled')),
    occurred_at ndq.source_time,
    published_at ndq.source_time,
    scheduled_for ndq.source_time,
    observed_at ndq.source_time NOT NULL,
    timezone_status text NOT NULL CHECK (timezone_status IN ('date_only', 'not_provided_by_source', 'offset_provided')),
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category),
    CHECK ((event_state = 'occurred' AND occurred_at IS NOT NULL AND scheduled_for IS NULL)
        OR (event_state = 'scheduled' AND scheduled_for IS NOT NULL AND occurred_at IS NULL)),
    CHECK (occurred_at IS NULL OR left(occurred_at, 10)::date <= left(observed_at, 10)::date),
    CHECK (occurred_at IS NULL OR
        (length(occurred_at) = 10 AND timezone_status = 'date_only') OR
        (length(occurred_at) > 10 AND occurred_at !~ '(Z|[+-]\d{2}:\d{2})$' AND timezone_status = 'not_provided_by_source') OR
        (occurred_at ~ '(Z|[+-]\d{2}:\d{2})$' AND timezone_status = 'offset_provided'))
);
CREATE TABLE ndq.contextual_milestones (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'milestone' CHECK (category = 'milestone'),
    context_kind text NOT NULL CHECK (context_kind IN ('institutional_context_not_process_event', 'ndq_observation_not_agency_event', 'institutional_communication_not_procurement_award', 'scheduled_date_not_confirmed_occurrence')),
    date_precision text NOT NULL CHECK (date_precision IN ('month', 'day')),
    period text NOT NULL,
    scheduled_for ndq.source_time,
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category),
    CHECK ((date_precision = 'day' AND length(period) = 10 AND ndq.valid_temporal(period)) OR
        (date_precision = 'month' AND period ~ '^\d{4}-\d{2}$' AND ndq.valid_temporal(period || '-01'))),
    CHECK ((context_kind = 'scheduled_date_not_confirmed_occurrence') = (scheduled_for IS NOT NULL))
);
CREATE TABLE ndq.status_observations (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'status' CHECK (category = 'status'),
    status_text text NOT NULL CHECK (status_text <> ''),
    institution text NOT NULL,
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category)
);
CREATE TABLE ndq.actors (
    actor_id text PRIMARY KEY,
    dossier_id text NOT NULL REFERENCES ndq.dossiers,
    name text NOT NULL CHECK (name <> ''),
    identity_state text NOT NULL DEFAULT 'name_only_unresolved' CHECK (identity_state = 'name_only_unresolved')
);
CREATE TABLE ndq.actor_roles (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'actor_role' CHECK (category = 'actor_role'),
    actor_id text NOT NULL REFERENCES ndq.actors,
    role_type text NOT NULL CHECK (role_type <> ''),
    role_date ndq.source_time,
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category)
);
-- An official act need not have its own dossier. No inferred acts are imported.
CREATE TABLE ndq.official_acts (
    act_id uuid PRIMARY KEY,
    institution text NOT NULL,
    namespace text NOT NULL,
    value text NOT NULL,
    reference_url ndq.https_url NOT NULL,
    UNIQUE (institution, namespace, value)
);
CREATE TABLE ndq.relationships (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'relationship' CHECK (category = 'relationship'),
    relation_type text NOT NULL CHECK (relation_type <> ''),
    origin text NOT NULL CHECK (origin IN ('official_document', 'editorial_proposal')),
    review_state text NOT NULL DEFAULT 'pending_review' CHECK (review_state IN ('pending_review', 'verified_documentary', 'rejected')),
    reviewed_by text,
    reviewed_at timestamptz,
    target_dossier_id text REFERENCES ndq.dossiers,
    target_act_id uuid REFERENCES ndq.official_acts,
    verification_status text NOT NULL CHECK (verification_status <> ''),
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category),
    CHECK (num_nonnulls(target_dossier_id, target_act_id) = 1),
    CHECK (review_state = 'pending_review' OR (nullif(reviewed_by, '') IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE TABLE ndq.documented_changes (
    revision_id uuid NOT NULL,
    observation_key text NOT NULL,
    category text NOT NULL DEFAULT 'change' CHECK (category = 'change'),
    attribute_path text NOT NULL CHECK (attribute_path <> ''),
    previous_value jsonb NOT NULL,
    new_value jsonb NOT NULL,
    detected_at ndq.source_time NOT NULL,
    PRIMARY KEY (revision_id, observation_key),
    FOREIGN KEY (revision_id, observation_key, category) REFERENCES ndq.observations (revision_id, observation_key, category)
);
CREATE TABLE ndq.editorial_reviews (
    revision_id uuid PRIMARY KEY REFERENCES ndq.dossier_revisions,
    review_state text NOT NULL DEFAULT 'pending_review' CHECK (review_state IN ('pending_review', 'changes_requested', 'approved_private')),
    origin_review_status text NOT NULL,
    reviewer text,
    assessment jsonb NOT NULL CHECK (jsonb_typeof(assessment) = 'object'),
    CHECK (review_state = 'pending_review' OR nullif(reviewer, '') IS NOT NULL)
);
CREATE TABLE ndq.editorial_basis (
    revision_id uuid NOT NULL REFERENCES ndq.editorial_reviews,
    fact_key text NOT NULL,
    PRIMARY KEY (revision_id, fact_key),
    FOREIGN KEY (revision_id, fact_key) REFERENCES ndq.facts
);
CREATE TABLE ndq.publication_states (
    revision_id uuid PRIMARY KEY REFERENCES ndq.dossier_revisions,
    state text NOT NULL CHECK (state IN ('private_preview_only', 'withheld'))
);
CREATE TABLE ndq.scheduled_checks (
    check_id uuid PRIMARY KEY,
    revision_id uuid NOT NULL,
    basis_observation_key text NOT NULL,
    due_at timestamptz NOT NULL,
    reason text NOT NULL CHECK (reason <> ''),
    idempotency_key text NOT NULL UNIQUE CHECK (idempotency_key <> ''),
    created_at timestamptz NOT NULL DEFAULT current_timestamp,
    FOREIGN KEY (revision_id, basis_observation_key) REFERENCES ndq.observations
);
CREATE INDEX scheduled_checks_due ON ndq.scheduled_checks (due_at);
CREATE TABLE ndq.check_results (
    result_id uuid PRIMARY KEY,
    check_id uuid NOT NULL REFERENCES ndq.scheduled_checks,
    run_id uuid REFERENCES ndq.collection_runs,
    recorded_at timestamptz NOT NULL DEFAULT current_timestamp,
    outcome text NOT NULL CHECK (outcome IN ('changes_detected', 'no_change_detected', 'source_unavailable', 'error', 'cancelled')),
    note text NOT NULL CHECK (note <> '')
);

CREATE VIEW ndq.latest_revisions AS
SELECT DISTINCT ON (dossier_id) * FROM ndq.dossier_revisions
ORDER BY dossier_id, revision_number DESC;

-- Identity/actor links may never silently cross dossier boundaries.
CREATE FUNCTION ndq.check_link_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_id text; linked_owner text;
BEGIN
    SELECT dossier_id INTO owner_id FROM ndq.dossier_revisions WHERE revision_id = NEW.revision_id;
    IF TG_TABLE_NAME = 'identity_observations' THEN
        SELECT dossier_id INTO linked_owner FROM ndq.official_identities
        WHERE institution = NEW.institution AND namespace = NEW.namespace AND value = NEW.value;
    ELSE
        SELECT dossier_id INTO linked_owner FROM ndq.actors WHERE actor_id = NEW.actor_id;
    END IF;
    IF owner_id IS DISTINCT FROM linked_owner THEN
        RAISE EXCEPTION 'Cross-dossier identity/actor attachment requires a reviewed model change';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER identity_owner BEFORE INSERT ON ndq.identity_observations
FOR EACH ROW EXECUTE FUNCTION ndq.check_link_owner();
CREATE TRIGGER actor_owner BEFORE INSERT ON ndq.actor_roles
FOR EACH ROW EXECUTE FUNCTION ndq.check_link_owner();

CREATE FUNCTION ndq.check_predecessor() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_number integer;
BEGIN
    IF NEW.previous_revision_id IS NOT NULL THEN
        SELECT revision_number INTO prior_number FROM ndq.dossier_revisions
        WHERE revision_id = NEW.previous_revision_id AND dossier_id = NEW.dossier_id;
        IF prior_number IS NULL OR prior_number + 1 <> NEW.revision_number THEN
            RAISE EXCEPTION 'Revision must immediately follow its documented predecessor';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER revision_predecessor BEFORE INSERT ON ndq.dossier_revisions
FOR EACH ROW EXECUTE FUNCTION ndq.check_predecessor();

-- All history is append-only. Corrections/retractions require a new revision.
CREATE FUNCTION ndq.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'NDQ history is append-only; insert a new revision/observation'; END $$;
DO $$ DECLARE t record; BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'ndq' LOOP
        EXECUTE format('CREATE TRIGGER preserve_history BEFORE UPDATE OR DELETE OR TRUNCATE ON ndq.%I FOR EACH STATEMENT EXECUTE FUNCTION ndq.reject_mutation()', t.tablename);
    END LOOP;
END $$;
