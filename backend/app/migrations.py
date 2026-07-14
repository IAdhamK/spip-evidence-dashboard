from __future__ import annotations

import hashlib
import sqlite3


MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "analysis_pipeline_v2_foundation",
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            content_type TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL,
            storage_status TEXT NOT NULL DEFAULT 'pending',
            pending_bytes BLOB,
            source_system TEXT NOT NULL DEFAULT 'smart_upload',
            source_reference TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            purge_after TEXT,
            UNIQUE (sha256, size_bytes)
        );

        CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            analysis_mode TEXT NOT NULL DEFAULT 'deep',
            pipeline_version TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            configuration_hash TEXT NOT NULL,
            total_units INTEGER NOT NULL DEFAULT 0,
            processed_units INTEGER NOT NULL DEFAULT 0,
            failed_units INTEGER NOT NULL DEFAULT 0,
            ocr_required_units INTEGER NOT NULL DEFAULT 0,
            coverage_percentage REAL NOT NULL DEFAULT 0,
            coverage_status TEXT NOT NULL DEFAULT 'unknown',
            primary_blocked INTEGER NOT NULL DEFAULT 1,
            block_reasons_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY (document_id) REFERENCES documents (id)
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_runs_status
        ON analysis_runs(status, created_at);

        CREATE INDEX IF NOT EXISTS idx_analysis_runs_document
        ON analysis_runs(document_id, created_at);

        CREATE TABLE IF NOT EXISTS analysis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            stage TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_events_run
        ON analysis_events(run_id, id);

        CREATE TABLE IF NOT EXISTS document_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            unit_key TEXT NOT NULL,
            unit_type TEXT NOT NULL,
            ordinal INTEGER,
            heading_path_json TEXT NOT NULL DEFAULT '[]',
            source_location_json TEXT NOT NULL DEFAULT '{}',
            text TEXT NOT NULL DEFAULT '',
            text_sha256 TEXT,
            char_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, unit_key),
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE INDEX IF NOT EXISTS idx_document_units_run
        ON document_units(run_id, ordinal, id);

        CREATE TABLE IF NOT EXISTS document_structures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            structure_type TEXT NOT NULL,
            structure_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE TABLE IF NOT EXISTS extracted_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            fact_key TEXT NOT NULL,
            claim TEXT NOT NULL,
            fact_type TEXT NOT NULL DEFAULT 'unknown',
            organization TEXT,
            period TEXT,
            confidence REAL,
            extraction_method TEXT NOT NULL DEFAULT 'deterministic',
            status TEXT NOT NULL DEFAULT 'extracted',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, fact_key),
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE TABLE IF NOT EXISTS fact_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            source_location_json TEXT NOT NULL DEFAULT '{}',
            source_quote TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (fact_id, unit_id, source_quote),
            FOREIGN KEY (fact_id) REFERENCES extracted_facts (id),
            FOREIGN KEY (unit_id) REFERENCES document_units (id)
        );

        CREATE TABLE IF NOT EXISTS mapping_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            kk_id TEXT NOT NULL,
            kode TEXT NOT NULL,
            detail_kode TEXT NOT NULL,
            retrieval_score REAL NOT NULL DEFAULT 0,
            mapping_score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'candidate',
            supporting_fact_ids_json TEXT NOT NULL DEFAULT '[]',
            reasons_json TEXT NOT NULL DEFAULT '[]',
            missing_evidence_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, kk_id, kode, detail_kode),
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE TABLE IF NOT EXISTS grade_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            mapping_candidate_id INTEGER NOT NULL,
            candidate_grade TEXT,
            grade_ceiling TEXT,
            rule_version TEXT NOT NULL,
            rule_trace_json TEXT NOT NULL DEFAULT '{}',
            missing_requirements_json TEXT NOT NULL DEFAULT '[]',
            primary_allowed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id),
            FOREIGN KEY (mapping_candidate_id) REFERENCES mapping_candidates (id)
        );

        CREATE TABLE IF NOT EXISTS verification_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            mapping_candidate_id INTEGER,
            verifier_type TEXT NOT NULL,
            status TEXT NOT NULL,
            findings_json TEXT NOT NULL DEFAULT '[]',
            source_coverage_ok INTEGER NOT NULL DEFAULT 0,
            grade_rule_ok INTEGER NOT NULL DEFAULT 0,
            period_ok INTEGER NOT NULL DEFAULT 0,
            organization_ok INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id),
            FOREIGN KEY (mapping_candidate_id) REFERENCES mapping_candidates (id)
        );

        CREATE TABLE IF NOT EXISTS human_review_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            mapping_candidate_id INTEGER,
            reviewer_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            original_mapping_json TEXT NOT NULL DEFAULT '{}',
            final_mapping_json TEXT NOT NULL DEFAULT '{}',
            reason TEXT NOT NULL,
            override_warnings_json TEXT NOT NULL DEFAULT '[]',
            pipeline_version TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id),
            FOREIGN KEY (mapping_candidate_id) REFERENCES mapping_candidates (id)
        );
        """,
    ),
    (
        2,
        "analysis_engine_execution_trace",
        """
        CREATE TABLE IF NOT EXISTS engine_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            engine_name TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            status TEXT NOT NULL,
            input_checksum TEXT NOT NULL DEFAULT '',
            input_refs_json TEXT NOT NULL DEFAULT '[]',
            output_refs_json TEXT NOT NULL DEFAULT '[]',
            coverage_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            output_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, engine_name, engine_version, input_checksum),
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE INDEX IF NOT EXISTS idx_engine_results_run
        ON engine_results(run_id, id);

        CREATE TABLE IF NOT EXISTS security_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            blocking INTEGER NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id),
            FOREIGN KEY (document_id) REFERENCES documents (id)
        );

        CREATE INDEX IF NOT EXISTS idx_security_findings_run
        ON security_findings(run_id, severity, id);
        """,
    ),
    (
        3,
        "analysis_durable_job_queue",
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'queued',
            file_name TEXT NOT NULL,
            content_type TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            payload BLOB,
            analysis_mode TEXT NOT NULL DEFAULT 'full_audit',
            run_id INTEGER,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            lease_until TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status
        ON analysis_jobs(status, created_at);
        """,
    ),
    (
        4,
        "cross_document_evidence_packages",
        """
        CREATE TABLE IF NOT EXISTS analysis_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            organization TEXT,
            period TEXT,
            primary_blocked INTEGER NOT NULL DEFAULT 1,
            block_reasons_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS analysis_package_members (
            package_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (package_id, run_id),
            FOREIGN KEY (package_id) REFERENCES analysis_packages (id),
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE TABLE IF NOT EXISTS package_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id INTEGER NOT NULL,
            kk_id TEXT NOT NULL,
            kode TEXT NOT NULL,
            detail_kode TEXT NOT NULL,
            organization TEXT NOT NULL DEFAULT 'unknown',
            period TEXT NOT NULL DEFAULT 'unknown',
            chain_json TEXT NOT NULL DEFAULT '{}',
            supporting_run_ids_json TEXT NOT NULL DEFAULT '[]',
            supporting_fact_ids_json TEXT NOT NULL DEFAULT '[]',
            safe_grade TEXT,
            contradictions_json TEXT NOT NULL DEFAULT '[]',
            missing_requirements_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'needs_human_review',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (package_id) REFERENCES analysis_packages (id)
        );

        CREATE INDEX IF NOT EXISTS idx_package_assessments_package
        ON package_assessments(package_id, kk_id, kode, detail_kode);

        CREATE TABLE IF NOT EXISTS package_engine_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id INTEGER NOT NULL,
            engine_name TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            status TEXT NOT NULL,
            output_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (package_id) REFERENCES analysis_packages (id)
        );
        """,
    ),
    (
        5,
        "domain_rule_governance",
        """
        CREATE TABLE IF NOT EXISTS domain_rule_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kk_id TEXT NOT NULL,
            kode TEXT NOT NULL,
            detail_kode TEXT NOT NULL,
            grade TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            rule_checksum TEXT NOT NULL,
            status TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            rule_definition_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (kk_id, kode, detail_kode, grade, rule_version)
        );

        CREATE INDEX IF NOT EXISTS idx_domain_rule_approvals_lookup
        ON domain_rule_approvals(kk_id, kode, detail_kode, grade, rule_version, status);
        """,
    ),
    (
        6,
        "package_human_review",
        """
        CREATE TABLE IF NOT EXISTS package_review_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id INTEGER NOT NULL,
            reviewer_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            assessment_snapshot_json TEXT NOT NULL DEFAULT '[]',
            pipeline_version TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (package_id) REFERENCES analysis_packages (id)
        );

        CREATE INDEX IF NOT EXISTS idx_package_review_decisions_package
        ON package_review_decisions(package_id, id);
        """,
    ),
    (
        7,
        "controlled_v2_upload_audit",
        """
        CREATE TABLE IF NOT EXISTS controlled_upload_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            mapping_candidate_id INTEGER NOT NULL,
            legacy_review_id INTEGER,
            reviewer_id TEXT NOT NULL,
            status TEXT NOT NULL,
            destination_json TEXT NOT NULL DEFAULT '{}',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id),
            FOREIGN KEY (mapping_candidate_id) REFERENCES mapping_candidates (id),
            FOREIGN KEY (legacy_review_id) REFERENCES smart_upload_reviews (id)
        );

        CREATE INDEX IF NOT EXISTS idx_controlled_upload_actions_run
        ON controlled_upload_actions(run_id, mapping_candidate_id, id);
        """,
    ),
    (
        8,
        "reverification_history",
        """
        ALTER TABLE grade_assessments ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE verification_results ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;

        CREATE INDEX IF NOT EXISTS idx_grade_assessments_active
        ON grade_assessments(run_id, is_active, mapping_candidate_id);
        CREATE INDEX IF NOT EXISTS idx_verification_results_active
        ON verification_results(run_id, is_active, mapping_candidate_id);
        """,
    ),
    (
        9,
        "evaluation_learning_reports",
        """
        CREATE TABLE IF NOT EXISTS evaluation_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_version TEXT NOT NULL,
            dataset_name TEXT NOT NULL,
            dataset_status TEXT NOT NULL,
            case_count INTEGER NOT NULL,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            report_sha256 TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (pipeline_version, dataset_name, report_sha256)
        );

        CREATE INDEX IF NOT EXISTS idx_evaluation_reports_pipeline
        ON evaluation_reports(pipeline_version, dataset_status, created_at);
        """,
    ),
    (
        10,
        "job_idempotency_and_legacy_backfill",
        """
        ALTER TABLE analysis_jobs ADD COLUMN dedupe_key TEXT;
        ALTER TABLE analysis_jobs ADD COLUMN heartbeat_at TEXT;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_jobs_dedupe_key
        ON analysis_jobs(dedupe_key)
        WHERE dedupe_key IS NOT NULL;

        CREATE TABLE IF NOT EXISTS legacy_analysis_imports (
            legacy_review_id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (legacy_review_id) REFERENCES smart_upload_reviews (id),
            FOREIGN KEY (document_id) REFERENCES documents (id),
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );
        """,
    ),
    (
        11,
        "unit_checkpoints_and_resume_lineage",
        """
        ALTER TABLE analysis_jobs ADD COLUMN resume_from_run_id INTEGER;
        ALTER TABLE analysis_runs ADD COLUMN resumed_from_run_id INTEGER;

        CREATE TABLE IF NOT EXISTS analysis_unit_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            unit_key TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            input_checksum TEXT NOT NULL DEFAULT '',
            output_refs_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, unit_key, stage, input_checksum),
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id)
        );

        CREATE INDEX IF NOT EXISTS idx_unit_checkpoints_run_stage
        ON analysis_unit_checkpoints(run_id, stage, status, unit_key);
        """,
    ),
    (
        12,
        "guided_expert_review_labels",
        """
        CREATE TABLE IF NOT EXISTS expert_review_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            reviewer_id TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (
                outcome IN ('confirmed', 'corrected', 'not_evidence', 'unsure')
            ),
            selected_mapping_candidate_id INTEGER,
            selected_fact_ids_json TEXT NOT NULL DEFAULT '[]',
            expected_mappings_json TEXT NOT NULL DEFAULT '[]',
            expected_source_locations_json TEXT NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL,
            dataset_status TEXT NOT NULL DEFAULT 'expert_candidate' CHECK (
                dataset_status IN ('pilot_unlabelled', 'expert_candidate', 'expert_gold')
            ),
            is_active INTEGER NOT NULL DEFAULT 1,
            supersedes_label_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id),
            FOREIGN KEY (selected_mapping_candidate_id) REFERENCES mapping_candidates (id),
            FOREIGN KEY (supersedes_label_id) REFERENCES expert_review_labels (id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_expert_review_labels_active_run
        ON expert_review_labels(run_id)
        WHERE is_active = 1;

        CREATE INDEX IF NOT EXISTS idx_expert_review_labels_status
        ON expert_review_labels(dataset_status, outcome, is_active, created_at);
        """,
    ),
    (
        13,
        "secure_batch_zip_intake",
        """
        ALTER TABLE analysis_jobs
        ADD COLUMN external_ai_allowed INTEGER NOT NULL DEFAULT 1;

        ALTER TABLE analysis_runs
        ADD COLUMN external_ai_allowed INTEGER NOT NULL DEFAULT 1;

        CREATE TABLE IF NOT EXISTS analysis_batch_intakes (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'processing',
            archive_file_name TEXT NOT NULL,
            archive_sha256 TEXT NOT NULL,
            archive_size_bytes INTEGER NOT NULL,
            analysis_mode TEXT NOT NULL DEFAULT 'full_audit',
            requested_limit INTEGER NOT NULL,
            selected_count INTEGER NOT NULL DEFAULT 0,
            enqueued_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            external_ai_allowed INTEGER NOT NULL DEFAULT 0,
            dedupe_key TEXT,
            audit_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_batch_intakes_dedupe
        ON analysis_batch_intakes(dedupe_key)
        WHERE dedupe_key IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_analysis_batch_intakes_status
        ON analysis_batch_intakes(status, created_at);

        CREATE TABLE IF NOT EXISTS analysis_batch_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            archive_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_kind TEXT NOT NULL DEFAULT 'unknown',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT,
            member_status TEXT NOT NULL DEFAULT 'discovered',
            job_id TEXT,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (batch_id, archive_path),
            FOREIGN KEY (batch_id) REFERENCES analysis_batch_intakes (id),
            FOREIGN KEY (job_id) REFERENCES analysis_jobs (id)
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_batch_members_batch
        ON analysis_batch_members(batch_id, ordinal);

        CREATE INDEX IF NOT EXISTS idx_analysis_batch_members_job
        ON analysis_batch_members(job_id);
        """,
    ),
    (
        14,
        "guided_governance_and_vision_consent",
        """
        CREATE TABLE IF NOT EXISTS domain_rule_approval_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kk_id TEXT NOT NULL,
            kode TEXT NOT NULL,
            detail_kode TEXT NOT NULL,
            grade TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            rule_checksum TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
            reviewer_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            rule_definition_json TEXT NOT NULL DEFAULT '{}',
            supersedes_event_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supersedes_event_id) REFERENCES domain_rule_approval_events (id)
        );

        CREATE INDEX IF NOT EXISTS idx_rule_approval_events_lookup
        ON domain_rule_approval_events(
            kk_id, kode, detail_kode, grade, rule_version, id
        );

        INSERT INTO domain_rule_approval_events (
            kk_id, kode, detail_kode, grade, rule_version, rule_checksum,
            status, reviewer_id, reason, rule_definition_json
        )
        SELECT
            kk_id, kode, detail_kode, grade, rule_version, rule_checksum,
            status, reviewer_id, reason, rule_definition_json
        FROM domain_rule_approvals
        WHERE NOT EXISTS (
            SELECT 1 FROM domain_rule_approval_events
        );

        CREATE TABLE IF NOT EXISTS vision_capability_probes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            api_surface TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
            report_sha256 TEXT NOT NULL UNIQUE,
            expected_tokens_json TEXT NOT NULL DEFAULT '[]',
            observed_text TEXT NOT NULL DEFAULT '',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT,
            reviewer_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_vision_probes_current
        ON vision_capability_probes(provider, model, api_surface, status, id);

        CREATE TABLE IF NOT EXISTS vision_governance_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL CHECK (
                scope IN ('capability_validation', 'external_data_processing')
            ),
            status TEXT NOT NULL CHECK (
                status IN ('approved', 'rejected', 'revoked')
            ),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            api_surface TEXT NOT NULL,
            sensitivity_scope TEXT NOT NULL DEFAULT 'restricted',
            evidence_sha256 TEXT,
            policy_version TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            supersedes_decision_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supersedes_decision_id) REFERENCES vision_governance_decisions (id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_governance_active_scope
        ON vision_governance_decisions(scope, provider, model, api_surface, policy_version)
        WHERE is_active = 1;

        CREATE INDEX IF NOT EXISTS idx_vision_governance_history
        ON vision_governance_decisions(scope, provider, model, api_surface, id);
        """,
    ),
    (
        15,
        "append_only_release_evidence_ledger",
        """
        CREATE TABLE IF NOT EXISTS analysis_release_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_cycle_id TEXT NOT NULL,
            release_version TEXT NOT NULL,
            stage TEXT NOT NULL CHECK (
                stage IN ('shadow', 'pilot', 'canary', 'general')
            ),
            decision TEXT NOT NULL CHECK (
                decision IN ('planned', 'started', 'passed', 'failed', 'rolled_back')
            ),
            pipeline_version TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            model TEXT NOT NULL,
            dataset_sha256 TEXT,
            comparison_report_sha256 TEXT,
            evaluation_report_id INTEGER,
            stable_cycle INTEGER NOT NULL DEFAULT 0,
            rollback_rehearsed INTEGER NOT NULL DEFAULT 0,
            critical_incident_count INTEGER NOT NULL DEFAULT 0,
            reviewer_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (evaluation_report_id) REFERENCES evaluation_reports (id)
        );

        CREATE INDEX IF NOT EXISTS idx_release_events_cycle
        ON analysis_release_events(release_cycle_id, id);

        CREATE INDEX IF NOT EXISTS idx_release_events_stage
        ON analysis_release_events(stage, decision, stable_cycle, id);

        CREATE TRIGGER IF NOT EXISTS trg_release_events_no_update
        BEFORE UPDATE ON analysis_release_events
        BEGIN
            SELECT RAISE(ABORT, 'analysis_release_events is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_release_events_no_delete
        BEFORE DELETE ON analysis_release_events
        BEGIN
            SELECT RAISE(ABORT, 'analysis_release_events is append-only');
        END;
        """,
    ),
    (
        16,
        "evaluation_report_dataset_provenance",
        """
        ALTER TABLE evaluation_reports ADD COLUMN dataset_sha256 TEXT;
        ALTER TABLE evaluation_reports
        ADD COLUMN generation_method TEXT NOT NULL DEFAULT 'manual_import';
        ALTER TABLE evaluation_reports ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}';

        CREATE INDEX IF NOT EXISTS idx_evaluation_reports_dataset
        ON evaluation_reports(pipeline_version, dataset_sha256, created_at);
        """,
    ),
    (
        17,
        "immutable_evaluation_reports",
        """
        CREATE TRIGGER IF NOT EXISTS trg_evaluation_reports_no_update
        BEFORE UPDATE ON evaluation_reports
        BEGIN
            SELECT RAISE(ABORT, 'evaluation_reports is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_evaluation_reports_no_delete
        BEFORE DELETE ON evaluation_reports
        BEGIN
            SELECT RAISE(ABORT, 'evaluation_reports is append-only');
        END;
        """,
    ),
    (
        18,
        "append_only_visual_review_workflow",
        """
        ALTER TABLE analysis_runs ADD COLUMN visual_review_checksum TEXT;

        CREATE TABLE IF NOT EXISTS visual_review_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            unit_key TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (
                decision IN ('confirmed', 'corrected', 'not_evidence', 'unsure')
            ),
            unit_text_sha256 TEXT NOT NULL,
            source_image_sha256 TEXT,
            reviewed_text TEXT NOT NULL DEFAULT '',
            reviewed_text_sha256 TEXT,
            semantic_description TEXT NOT NULL DEFAULT '',
            source_location_json TEXT NOT NULL DEFAULT '{}',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            reviewer_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            attested INTEGER NOT NULL DEFAULT 1 CHECK (attested = 1),
            supersedes_decision_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES analysis_runs (id),
            FOREIGN KEY (unit_id) REFERENCES document_units (id),
            FOREIGN KEY (supersedes_decision_id) REFERENCES visual_review_decisions (id)
        );

        CREATE INDEX IF NOT EXISTS idx_visual_review_decisions_unit
        ON visual_review_decisions(run_id, unit_key, id);

        CREATE INDEX IF NOT EXISTS idx_visual_review_decisions_reviewer
        ON visual_review_decisions(reviewer_id, created_at, id);

        CREATE TRIGGER IF NOT EXISTS trg_visual_review_decisions_no_update
        BEFORE UPDATE ON visual_review_decisions
        BEGIN
            SELECT RAISE(ABORT, 'visual_review_decisions is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_visual_review_decisions_no_delete
        BEFORE DELETE ON visual_review_decisions
        BEGIN
            SELECT RAISE(ABORT, 'visual_review_decisions is append-only');
        END;
        """,
    ),
    (
        19,
        "typed_visual_and_ocr_rescue_reviews",
        """
        ALTER TABLE visual_review_decisions
        ADD COLUMN review_kind TEXT NOT NULL DEFAULT 'visual_semantics'
        CHECK (review_kind IN ('visual_semantics', 'ocr_rescue'));

        CREATE INDEX IF NOT EXISTS idx_visual_review_decisions_kind
        ON visual_review_decisions(review_kind, run_id, unit_key, id);
        """,
    ),
    (
        20,
        "external_payload_storage_metadata",
        """
        ALTER TABLE documents ADD COLUMN payload_storage_backend TEXT NOT NULL DEFAULT 'database';
        ALTER TABLE documents ADD COLUMN payload_storage_key TEXT;
        ALTER TABLE documents ADD COLUMN payload_storage_sha256 TEXT;
        ALTER TABLE documents ADD COLUMN payload_storage_size_bytes INTEGER;
        ALTER TABLE documents ADD COLUMN payload_storage_created_at TEXT;

        ALTER TABLE analysis_jobs ADD COLUMN payload_storage_backend TEXT NOT NULL DEFAULT 'database';
        ALTER TABLE analysis_jobs ADD COLUMN payload_storage_key TEXT;
        ALTER TABLE analysis_jobs ADD COLUMN payload_storage_sha256 TEXT;
        ALTER TABLE analysis_jobs ADD COLUMN payload_storage_size_bytes INTEGER;
        ALTER TABLE analysis_jobs ADD COLUMN payload_storage_created_at TEXT;

        CREATE INDEX IF NOT EXISTS idx_documents_payload_storage
        ON documents(payload_storage_backend, payload_storage_key);

        CREATE INDEX IF NOT EXISTS idx_analysis_jobs_payload_storage
        ON analysis_jobs(payload_storage_backend, payload_storage_key);
        """,
    ),
    (
        21,
        "analysis_worker_leader_lease",
        """
        CREATE TABLE IF NOT EXISTS analysis_worker_leases (
            lease_name TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            heartbeat_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            lease_until TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_worker_leases_expiry
        ON analysis_worker_leases(lease_until);
        """,
    ),
    (
        22,
        "shadow_comparison_ledger",
        """
        CREATE TABLE IF NOT EXISTS analysis_shadow_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_review_id INTEGER NOT NULL,
            v2_job_id TEXT NOT NULL,
            v2_run_id INTEGER,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
            comparison_json TEXT NOT NULL DEFAULT '{}',
            report_sha256 TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            UNIQUE (legacy_review_id),
            FOREIGN KEY (legacy_review_id) REFERENCES smart_upload_reviews (id),
            FOREIGN KEY (v2_job_id) REFERENCES analysis_jobs (id),
            FOREIGN KEY (v2_run_id) REFERENCES analysis_runs (id)
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_shadow_pairs_job
        ON analysis_shadow_pairs(v2_job_id, status);

        CREATE INDEX IF NOT EXISTS idx_analysis_shadow_pairs_run
        ON analysis_shadow_pairs(v2_run_id, status);
        """,
    ),
    (
        23,
        "legacy_pipeline_usage_telemetry",
        """
        CREATE TABLE IF NOT EXISTS legacy_pipeline_usage_daily (
            usage_date TEXT NOT NULL,
            usage_kind TEXT NOT NULL,
            source TEXT NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0 CHECK (call_count >= 0),
            first_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (usage_date, usage_kind, source)
        );

        CREATE INDEX IF NOT EXISTS idx_legacy_pipeline_usage_last_used
        ON legacy_pipeline_usage_daily(last_used_at, usage_kind, source);
        """,
    ),
    (
        24,
        "expert_gold_retrieval_feedback_registry",
        """
        CREATE TABLE IF NOT EXISTS retrieval_feedback_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_sha256 TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            learning_version TEXT NOT NULL,
            parameter_catalog_sha256 TEXT NOT NULL,
            registry_sha256 TEXT NOT NULL,
            expert_gold_case_count INTEGER NOT NULL DEFAULT 0 CHECK (expert_gold_case_count >= 0),
            source_label_count INTEGER NOT NULL DEFAULT 0 CHECK (source_label_count >= 0),
            term_count INTEGER NOT NULL DEFAULT 0 CHECK (term_count >= 0),
            minimum_document_support INTEGER NOT NULL CHECK (minimum_document_support >= 2),
            minimum_precision REAL NOT NULL CHECK (minimum_precision >= 0 AND minimum_precision <= 1),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (
                dataset_sha256, pipeline_version, learning_version,
                parameter_catalog_sha256
            )
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_retrieval_feedback_snapshot_active
        ON retrieval_feedback_snapshots(is_active)
        WHERE is_active = 1;

        CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_snapshot_dataset
        ON retrieval_feedback_snapshots(dataset_sha256, pipeline_version, created_at);

        CREATE TABLE IF NOT EXISTS retrieval_feedback_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            parameter_id INTEGER NOT NULL,
            kk_id TEXT NOT NULL,
            kode TEXT NOT NULL,
            detail_kode TEXT NOT NULL,
            term_sha256 TEXT NOT NULL,
            document_support INTEGER NOT NULL CHECK (document_support >= 2),
            observed_document_count INTEGER NOT NULL CHECK (observed_document_count >= document_support),
            precision REAL NOT NULL CHECK (precision >= 0 AND precision <= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (snapshot_id, kk_id, kode, detail_kode, term_sha256),
            FOREIGN KEY (snapshot_id) REFERENCES retrieval_feedback_snapshots (id)
        );

        CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_terms_snapshot_parameter
        ON retrieval_feedback_terms(snapshot_id, kk_id, kode, detail_kode, term_sha256);

        CREATE TRIGGER IF NOT EXISTS trg_retrieval_feedback_snapshots_no_delete
        BEFORE DELETE ON retrieval_feedback_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'retrieval feedback snapshots are append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_retrieval_feedback_snapshots_immutable_fields
        BEFORE UPDATE ON retrieval_feedback_snapshots
        WHEN NEW.dataset_sha256 IS NOT OLD.dataset_sha256
          OR NEW.pipeline_version IS NOT OLD.pipeline_version
          OR NEW.learning_version IS NOT OLD.learning_version
          OR NEW.parameter_catalog_sha256 IS NOT OLD.parameter_catalog_sha256
          OR NEW.registry_sha256 IS NOT OLD.registry_sha256
          OR NEW.expert_gold_case_count IS NOT OLD.expert_gold_case_count
          OR NEW.source_label_count IS NOT OLD.source_label_count
          OR NEW.term_count IS NOT OLD.term_count
          OR NEW.minimum_document_support IS NOT OLD.minimum_document_support
          OR NEW.minimum_precision IS NOT OLD.minimum_precision
          OR NEW.created_at IS NOT OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'retrieval feedback snapshot fields are immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_retrieval_feedback_terms_no_update
        BEFORE UPDATE ON retrieval_feedback_terms
        BEGIN
            SELECT RAISE(ABORT, 'retrieval feedback terms are immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_retrieval_feedback_terms_no_delete
        BEFORE DELETE ON retrieval_feedback_terms
        BEGIN
            SELECT RAISE(ABORT, 'retrieval feedback terms are append-only');
        END;
        """,
    ),
    (
        25,
        "separate_learning_and_evaluation_gold",
        """
        ALTER TABLE expert_review_labels
        ADD COLUMN dataset_partition TEXT NOT NULL DEFAULT 'evaluation'
        CHECK (dataset_partition IN ('evaluation', 'learning'));

        CREATE INDEX IF NOT EXISTS idx_expert_review_labels_partition
        ON expert_review_labels(
            dataset_status, dataset_partition, is_active, created_at
        );

        UPDATE retrieval_feedback_snapshots
        SET is_active = 0
        WHERE is_active = 1;
        """,
    ),
    (
        26,
        "server_derived_evaluation_release_authority",
        """
        DROP TRIGGER IF EXISTS trg_evaluation_reports_no_update;
        DROP TRIGGER IF EXISTS trg_evaluation_reports_no_delete;

        ALTER TABLE evaluation_reports
        ADD COLUMN release_authority INTEGER NOT NULL DEFAULT 0
        CHECK (release_authority IN (0, 1));

        CREATE INDEX IF NOT EXISTS idx_evaluation_reports_authority
        ON evaluation_reports(
            pipeline_version, release_authority, dataset_sha256, created_at
        );

        CREATE TRIGGER IF NOT EXISTS trg_evaluation_reports_no_update
        BEFORE UPDATE ON evaluation_reports
        BEGIN
            SELECT RAISE(ABORT, 'evaluation_reports is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_evaluation_reports_no_delete
        BEFORE DELETE ON evaluation_reports
        BEGIN
            SELECT RAISE(ABORT, 'evaluation_reports is append-only');
        END;
        """,
    ),
    (
        27,
        "controlled_upload_idempotency_reservation",
        """
        ALTER TABLE controlled_upload_actions
        ADD COLUMN idempotency_key TEXT;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_controlled_upload_actions_idempotency
        ON controlled_upload_actions(idempotency_key)
        WHERE idempotency_key IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_controlled_upload_actions_status_created
        ON controlled_upload_actions(status, created_at);
        """,
    ),
    (
        28,
        "controlled_upload_two_person_reconciliation",
        """
        CREATE TABLE IF NOT EXISTS controlled_upload_reconciliation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id INTEGER NOT NULL,
            reviewer_id TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (
                outcome IN (
                    'confirmed_uploaded',
                    'confirmed_not_uploaded',
                    'needs_investigation'
                )
            ),
            reason TEXT NOT NULL,
            attested INTEGER NOT NULL DEFAULT 1 CHECK (attested = 1),
            supersedes_event_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (action_id) REFERENCES controlled_upload_actions (id),
            FOREIGN KEY (supersedes_event_id)
                REFERENCES controlled_upload_reconciliation_events (id)
        );

        CREATE INDEX IF NOT EXISTS idx_controlled_upload_reconciliation_action
        ON controlled_upload_reconciliation_events(action_id, id);

        CREATE INDEX IF NOT EXISTS idx_controlled_upload_reconciliation_reviewer
        ON controlled_upload_reconciliation_events(action_id, reviewer_id, id);

        CREATE TRIGGER IF NOT EXISTS trg_controlled_upload_reconciliation_no_update
        BEFORE UPDATE ON controlled_upload_reconciliation_events
        BEGIN
            SELECT RAISE(ABORT, 'controlled_upload_reconciliation_events is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_controlled_upload_reconciliation_no_delete
        BEFORE DELETE ON controlled_upload_reconciliation_events
        BEGIN
            SELECT RAISE(ABORT, 'controlled_upload_reconciliation_events is append-only');
        END;
        """,
    ),
    (
        29,
        "fact_evidence_role_provenance",
        """
        ALTER TABLE extracted_facts
        ADD COLUMN evidence_role TEXT NOT NULL DEFAULT 'context';

        ALTER TABLE extracted_facts
        ADD COLUMN evidence_role_method TEXT NOT NULL DEFAULT 'legacy_default_v1';

        CREATE INDEX IF NOT EXISTS idx_extracted_facts_run_evidence_role
        ON extracted_facts(run_id, evidence_role, id);
        """,
    ),
    (
        30,
        "expert_template_expectation",
        """
        ALTER TABLE expert_review_labels
        ADD COLUMN expected_template_status TEXT NOT NULL DEFAULT 'not_assessed';

        CREATE INDEX IF NOT EXISTS idx_expert_review_labels_template_status
        ON expert_review_labels(
            dataset_status, dataset_partition, expected_template_status, is_active
        );
        """,
    ),
    (
        31,
        "advanced_rag_candidate_ranking",
        """
        ALTER TABLE mapping_candidates
        ADD COLUMN rag_rank INTEGER;

        ALTER TABLE mapping_candidates
        ADD COLUMN rag_relevance REAL;

        ALTER TABLE mapping_candidates
        ADD COLUMN rag_method TEXT;

        CREATE INDEX IF NOT EXISTS idx_mapping_candidates_rag_rank
        ON mapping_candidates(run_id, rag_rank, mapping_score DESC, id);
        """,
    ),
    (
        32,
        "document_family_confidence_and_grade_gate",
        """
        ALTER TABLE mapping_candidates
        ADD COLUMN raw_retrieval_score REAL NOT NULL DEFAULT 0;

        ALTER TABLE mapping_candidates
        ADD COLUMN calibrated_decision_confidence REAL NOT NULL DEFAULT 0;

        ALTER TABLE mapping_candidates
        ADD COLUMN confidence_components_json TEXT NOT NULL DEFAULT '{}';

        ALTER TABLE mapping_candidates
        ADD COLUMN decision_status TEXT NOT NULL DEFAULT 'needs_review';

        ALTER TABLE mapping_candidates
        ADD COLUMN document_family TEXT NOT NULL DEFAULT 'unknown';

        ALTER TABLE mapping_candidates
        ADD COLUMN document_role TEXT NOT NULL DEFAULT 'reject';

        ALTER TABLE mapping_candidates
        ADD COLUMN family_parameter_compatible INTEGER NOT NULL DEFAULT 0;

        ALTER TABLE mapping_candidates
        ADD COLUMN grade_eligible INTEGER NOT NULL DEFAULT 0;

        ALTER TABLE mapping_candidates
        ADD COLUMN grade_status TEXT NOT NULL DEFAULT 'blocked';

        ALTER TABLE mapping_candidates
        ADD COLUMN grade_block_reasons_json TEXT NOT NULL DEFAULT '[]';

        ALTER TABLE grade_assessments
        ADD COLUMN grade_eligible INTEGER NOT NULL DEFAULT 0;

        ALTER TABLE grade_assessments
        ADD COLUMN grade_status TEXT NOT NULL DEFAULT 'blocked';

        ALTER TABLE grade_assessments
        ADD COLUMN grade_block_reasons_json TEXT NOT NULL DEFAULT '[]';

        CREATE INDEX IF NOT EXISTS idx_mapping_candidates_family_decision
        ON mapping_candidates(run_id, document_family, decision_status, calibrated_decision_confidence DESC);
        """,
    ),
]


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        int(row[0])
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (version, name),
        )
    backfill_legacy_reviews(conn)
    backfill_payload_storage_metadata(conn)


def backfill_payload_storage_metadata(conn: sqlite3.Connection) -> int:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if not {"documents", "analysis_jobs"} <= tables:
        return 0
    updated = 0
    documents = conn.execute(
        """
        SELECT id, pending_bytes, size_bytes, sha256
        FROM documents
        WHERE pending_bytes IS NOT NULL
          AND (payload_storage_sha256 IS NULL OR payload_storage_size_bytes IS NULL)
        """
    ).fetchall()
    for row in documents:
        item = dict(row)
        payload = bytes(item["pending_bytes"])
        observed_sha256 = hashlib.sha256(payload).hexdigest()
        if len(payload) != int(item["size_bytes"]) or observed_sha256 != str(item["sha256"]):
            raise RuntimeError(
                f"Document #{item['id']} gagal integrity check saat Migration V20."
            )
        conn.execute(
            """
            UPDATE documents
            SET payload_storage_backend = 'database',
                payload_storage_sha256 = ?, payload_storage_size_bytes = ?,
                payload_storage_created_at = COALESCE(payload_storage_created_at, created_at)
            WHERE id = ?
            """,
            (observed_sha256, len(payload), int(item["id"])),
        )
        updated += 1

    jobs = conn.execute(
        """
        SELECT id, payload, size_bytes
        FROM analysis_jobs
        WHERE payload IS NOT NULL
          AND (payload_storage_sha256 IS NULL OR payload_storage_size_bytes IS NULL)
        """
    ).fetchall()
    for row in jobs:
        item = dict(row)
        payload = bytes(item["payload"])
        if len(payload) != int(item["size_bytes"]):
            raise RuntimeError(
                f"Analysis job {item['id']} gagal size check saat Migration V20."
            )
        conn.execute(
            """
            UPDATE analysis_jobs
            SET payload_storage_backend = 'database',
                payload_storage_sha256 = ?, payload_storage_size_bytes = ?,
                payload_storage_created_at = COALESCE(payload_storage_created_at, created_at)
            WHERE id = ?
            """,
            (hashlib.sha256(payload).hexdigest(), len(payload), str(item["id"])),
        )
        updated += 1
    return updated


def backfill_legacy_reviews(conn: sqlite3.Connection) -> int:
    """Create traceable legacy runs without inventing units, facts, or model decisions."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if not {"smart_upload_reviews", "legacy_analysis_imports"} <= tables:
        return 0
    rows = conn.execute(
        """
        SELECT smart_upload_reviews.*
        FROM smart_upload_reviews
        LEFT JOIN legacy_analysis_imports
          ON legacy_analysis_imports.legacy_review_id = smart_upload_reviews.id
        WHERE legacy_analysis_imports.legacy_review_id IS NULL
          AND COALESCE(smart_upload_reviews.ai_status, '') != 'v2_controlled'
        ORDER BY smart_upload_reviews.id
        """
    ).fetchall()
    imported = 0
    for row in rows:
        review = dict(row)
        sha256 = str(review.get("file_sha256") or f"legacy-review-{review['id']}")
        size_bytes = int(review.get("size_bytes") or 0)
        conn.execute(
            """
            INSERT INTO documents (
                file_name, content_type, size_bytes, sha256, storage_status,
                source_system, source_reference
            )
            VALUES (?, ?, ?, ?, 'legacy_metadata', 'legacy_import', ?)
            ON CONFLICT(sha256, size_bytes) DO NOTHING
            """,
            (
                review.get("file_name") or f"legacy-review-{review['id']}",
                review.get("content_type"),
                size_bytes,
                sha256,
                f"smart_upload_reviews:{review['id']}",
            ),
        )
        document = conn.execute(
            "SELECT id FROM documents WHERE sha256 = ? AND size_bytes = ?",
            (sha256, size_bytes),
        ).fetchone()
        if not document:
            continue
        upload_status = str(review.get("upload_status") or "pending")
        run_status = {
            "uploaded": "approved",
            "uploaded_primary": "approved",
            "rejected": "rejected",
        }.get(upload_status, "review_required")
        cursor = conn.execute(
            """
            INSERT INTO analysis_runs (
                document_id, status, analysis_mode, pipeline_version, parser_version,
                rule_version, prompt_version, provider, model, configuration_hash,
                coverage_status, primary_blocked, block_reasons_json, created_at, finished_at
            )
            VALUES (?, ?, 'legacy_import', 'legacy', 'legacy', 'legacy', 'legacy',
                    NULL, NULL, ?, 'unknown', ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
            """,
            (
                int(document["id"]),
                run_status,
                f"legacy-review-{review['id']}",
                1,
                '["Imported legacy review has no V2 coverage/provenance and cannot authorize V2 upload."]',
                review.get("created_at"),
                review.get("confirmed_at"),
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO legacy_analysis_imports (legacy_review_id, document_id, run_id)
            VALUES (?, ?, ?)
            """,
            (int(review["id"]), int(document["id"]), run_id),
        )
        if run_status in {"approved", "rejected"}:
            decision = "approve" if run_status == "approved" else "reject"
            mapping_json = review.get("confirmed_candidate_json") or "{}"
            conn.execute(
                """
                INSERT INTO human_review_decisions (
                    run_id, mapping_candidate_id, reviewer_id, decision,
                    original_mapping_json, final_mapping_json, reason,
                    override_warnings_json, pipeline_version, rule_version, created_at
                )
                VALUES (?, NULL, 'legacy_import', ?, ?, ?, ?, '[]', 'legacy', 'legacy',
                        COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    run_id,
                    decision,
                    mapping_json,
                    mapping_json,
                    f"Imported from legacy smart upload review #{review['id']} ({upload_status}).",
                    review.get("confirmed_at") or review.get("created_at"),
                ),
            )
        imported += 1
    return imported
