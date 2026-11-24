-- SEO Intelligence evidence store: PostgreSQL equivalent of seo_store.DDL (schema version 1).
-- Apply to a DEDICATED database (not the WordPress, Workspace or Research databases) by the database owner.
-- Not applied anywhere by this repository: staging/production creation needs owner authorization and the
-- infrastructure safeguards in docs/SEO-STUDIO.md (backups, off-host retention, verified restore, monitoring).
--
-- Roles (create with passwords held server-side only, never in this repository):
--   seo_owner   : owns objects; used only for migrations.          NOLOGIN outside migration windows.
--   seo_writer  : the importer (baseline, verification runs, connector pulls). INSERT + SELECT only.
--   seo_reader  : SEO Studio server. SELECT only.

CREATE SCHEMA IF NOT EXISTS seo AUTHORIZATION seo_owner;
SET search_path = seo;

CREATE TABLE meta (key text PRIMARY KEY, value text NOT NULL);
CREATE TABLE run (
  run_id text PRIMARY KEY, label text NOT NULL, kind text NOT NULL, imported_at_utc text NOT NULL,
  observed_window jsonb, history_sha256 char(64) NOT NULL UNIQUE, property_scope text NOT NULL,
  receipt jsonb, extensions jsonb NOT NULL);
CREATE TABLE artifact (
  run_id text NOT NULL REFERENCES run, source_kind text NOT NULL, source_sha256 text NOT NULL,
  source_file text, details jsonb NOT NULL, PRIMARY KEY (run_id, source_kind, source_sha256));
CREATE TABLE observation (
  run_id text NOT NULL REFERENCES run, observation_id text NOT NULL, url text NOT NULL, source_kind text NOT NULL,
  source_sha256 text NOT NULL, source_record integer, source_time_raw text, facts jsonb NOT NULL,
  PRIMARY KEY (run_id, observation_id));
CREATE INDEX observation_url ON observation (run_id, url);
CREATE TABLE page (
  run_id text NOT NULL REFERENCES run, url text NOT NULL, http_status integer, redirect_hops integer,
  sitemap_member integer, wordpress_status text, post_type text, verification_state text, noindex integer,
  canonical_self integer, has_description integer, h1_count integer, structured_data text, ttfb_ms integer,
  x_cache text, measured_findings integer NOT NULL, prior_findings integer NOT NULL, broken_links_out integer NOT NULL,
  search_impressions integer, search_clicks integer, google_coverage text, PRIMARY KEY (run_id, url));
CREATE TABLE finding (
  run_id text NOT NULL REFERENCES run, url text NOT NULL, priority text NOT NULL, action text NOT NULL,
  observation_id text NOT NULL, evidence_class text NOT NULL CHECK (evidence_class IN ('measured', 'prior')),
  origin text);
CREATE INDEX finding_run ON finding (run_id, evidence_class, priority);
CREATE TABLE change_event (
  run_id text NOT NULL REFERENCES run, batch text NOT NULL, state text NOT NULL, details jsonb NOT NULL,
  PRIMARY KEY (run_id, batch));
CREATE TABLE recommendation (
  run_id text NOT NULL REFERENCES run, recommendation_id text NOT NULL, kind text NOT NULL, subject text NOT NULL,
  summary text NOT NULL, evidence_class text NOT NULL CHECK (evidence_class = 'inferred'), basis jsonb NOT NULL,
  approval text NOT NULL CHECK (approval = 'required'), status text NOT NULL,
  PRIMARY KEY (run_id, recommendation_id));
CREATE TABLE search_performance (
  run_id text NOT NULL REFERENCES run, engine text NOT NULL, url text NOT NULL, start_date date NOT NULL,
  end_date date NOT NULL, clicks integer, impressions integer, ctr double precision, position double precision,
  PRIMARY KEY (run_id, engine, url, start_date, end_date));

INSERT INTO meta VALUES ('schema_version', '1');

-- Append-only: enforced by privileges AND by trigger (so even the owner cannot rewrite history by accident).
CREATE FUNCTION refuse_change() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION 'append-only evidence store: % on % refused', TG_OP, TG_TABLE_NAME; END $$;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['meta','run','artifact','observation','page','finding','change_event','recommendation',
                           'search_performance'] LOOP
    EXECUTE format('CREATE TRIGGER %I_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON seo.%I '
                   'FOR EACH STATEMENT EXECUTE FUNCTION seo.refuse_change()', t, t);
  END LOOP; END $$;

REVOKE ALL ON SCHEMA seo FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA seo FROM PUBLIC;
GRANT USAGE ON SCHEMA seo TO seo_writer, seo_reader;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA seo TO seo_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA seo TO seo_reader;
REVOKE UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA seo FROM seo_writer, seo_reader;
