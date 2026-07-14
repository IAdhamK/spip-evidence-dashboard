# Changeset Review Manifest — Document Intelligence V2

Status: logical review map for the current dirty worktree; **not** a claim that every modified file was created in one change. Existing user changes must be preserved and reviewed before staging.

## Recommended review order

| Cluster | Purpose | Primary files | Evidence |
|---|---|---|---|
| 1. Persistence and lifecycle | Forward-only schema, payload retention, worker/shadow/legacy-usage/retrieval-feedback ledger, fact evidence-role provenance, expert template expectation, controlled-upload reservation/reconciliation, application-owned worker lifespan | `backend/app/migrations.py`, `backend/app/analysis/repository.py`, `backend/app/analysis/payload_storage.py`, `backend/app/lifecycle.py`, `backend/app/main.py`, `scripts/analysis_db_backup.py` | Migration V1–V30 on a copy of the actual V7 database; evidence-role/template upgrade regression; controlled-upload unique-key/concurrency/two-person append-only reconciliation; `integrity_check=ok`; paired restore; start/stop/error-cleanup regression; Python 3.12 warning gate |
| 2. Queue and process safety | Single-replica production contract plus capability-gated PostgreSQL/Redis scale-out adapters | `backend/app/analysis/queue_backend.py`, `backend/app/analysis/jobs.py`, `backend/app/analysis/repository.py`, `backend/app/config.py` | Dual claimant, notified-ID atomic claim, second-manager block, expiry/reclaim, PostgreSQL capability activation, Redis non-authoritative FIFO/fallback/TLS guards, SQLite no-connect false-readiness regression, Redis 8.8.0 smoke, redis-py 8.0.1 Python 3.12 image smoke |
| 3. Multi-engine analysis | Intake, parsing, OCR/vision, facts, compute routing, retrieval, constrained mapping reasoning, grading, verification | `backend/app/analysis/orchestrator.py`, `routing.py`, `mapping_reasoning.py`, `provider.py`, `document_map.py`, `local_ocr.py`, `vision.py`, `facts.py`, `domain/` | Complexity/risk/margin routing regression, model demotion-only and deterministic-verifier no-override tests, backend regression, corpus audit, Office/Tesseract container evidence |
| 4. Human review and governance | Guided/visual review, two-person expert gold, rule/vision consent | `backend/app/analysis/routes.py`, `visual_review.py`, `governance.py`, `reviewer_identity.py` | Append-only/checksum tests, trusted identity tests, no-code UI build |
| 5. Evaluation and rollout | Holdout expert evaluation, conservative partitioned feedback learning, shadow comparison, authoritative release hard gates | `backend/app/analysis/expert_evaluation.py`, `facts.py`, `provider.py`, `learning.py`, `rollout.py`, `shadow.py` | Deterministic content-free retrieval/mapping/source/evidence-role/template/grade/abstention/latency/cost reports, label coverage gates, immutable authority/fingerprint snapshots, Evaluation/Learning separation, manual-report bypass rejection, release-time recomputation, automatic V1↔V2 linkage, minimum-50 shadow gate, stale checksum tests |
| 6. Legacy compatibility boundary | Keep V1 authoritative/fallback and isolate parser, recommendation domain, AI normalization, candidate ranking, duplicate/upload support, text utility, and transport/provider responsibilities | `backend/app/analysis/legacy_bridge.py`, `backend/app/legacy_ai_transport.py`, `backend/app/legacy_ai_normalization.py`, `backend/app/legacy_candidate_ranking.py`, `backend/app/legacy_document_extraction.py`, `backend/app/legacy_recommendation_domain.py`, `backend/app/legacy_text_utils.py`, `backend/app/legacy_upload_support.py`, `backend/app/routes.py`, `backend/app/smart_upload.py` | Parser parity; template/maturity/reasoning/XLSX-reference/batch-gate parity; narrative/placement clamp and transport patchability; immutable seed/ranking; exact duplicate block; facade function-ownership and dependency guards; public import compatibility; single-call controlled-upload concurrency, idempotent-success, dan ambiguous-result lock regression |
| 7. Frontend composition | No-code upload/review/governance/readiness surfaces | `frontend/src/main.jsx`, `frontend/src/features/SmartUploadPage.jsx`, `frontend/src/features/smart-upload/` (ZIP intake, controls/progress, link crawl, V2 result, batch panels, legacy result, utilities), `GovernancePage.jsx`, `GuidedReviewPage.jsx`, `VisualReviewPage.jsx`, rollout/readiness modules, shared feedback/status/formatters/source-location, `frontend/src/styles/main.css` | Smart Upload root 478 lines; endpoint ownership across root/submodules; presentational no-API/no-hook and line-budget guards; explicit queued-state icon import; `npm run check:boundaries`; production build; root/assets/config/readiness HTTP 200 |
| 8. Operations | Metrics, alerting, structured logs, proxy, backup, incident response | `ops/`, `backend/app/analysis/reviewer_identity.py`, `backend/app/analysis/structured_logging.py`, `backend/app/analysis/storage_evidence.py`, `backend/app/analysis/storage_attestation_cli.py`, `scripts/issue_storage_encryption_attestation.py`, `scripts/probe_ocr_resource_budget.py`, `scripts/run_incident_drill.py`, `scripts/validate_production_profile.py`, `docker-compose.yml` | Trusted identity+role RBAC with endpoint scopes and Nginx spoof protection; allowlisted content-free JSON lifecycle logs with run-bound terminal events; seventeen Prometheus rules including rolling-hour cost anomaly; production-profile validator v7 untuk RBAC, SQLite integrity/exact V30/evidence-role/template/idempotency/reconciliation; hierarchical OCR metrics, signed storage attestation, incident drill, dan Docker config/build |
| 9. Documentation and eval fixtures | Reproducible handover and functional gates | `docs/`, `evals/`, `README.md`, `.github/workflows/quality.yml`, `scripts/validate_handover_docs.py`, `scripts/run_functional_acceptance.py` | Roadmap/completion audit aligned to 251 backend tests; Functional Acceptance v1 covers operational/fixture six-format routing, 16-engine sequencing, exact source provenance, partial fail-closed, focused regression, and mocked upload; handover validator covers 57 OpenAPI paths, 61 operations, 55 role-secured/6 proxy-boundary authorization classifications, and 46 DDL tables |

## Boundary invariants

1. `app.analysis` may import `app.smart_upload` only inside `legacy_bridge.py`.
2. External queue names and `ANALYSIS_EXPECTED_REPLICAS>1` remain fail-closed while canonical persistence is SQLite.
3. Shadow agreement has `quality_authority=none_without_expert_gold`; it cannot open a release gate by itself.
4. Real upload and external vision remain off until their independent governance gates pass.
5. Payload corruption, missing external files, unsafe permissions, stale checksums, and incomplete coverage never fall back silently.
6. The legacy pipeline remains available for rollback and authoritative during shadow mode.

## Verification commands

```bash
PYTHONPATH=backend .venv/bin/python -W error::ResourceWarning -m unittest discover -s backend/tests -p 'test_*.py' -q
PYTHONPATH=backend .venv/bin/python scripts/validate_handover_docs.py
cd frontend && npm run check:boundaries && npm run build
docker compose config --quiet
docker run --rm --entrypoint promtool -v "$PWD/ops/prometheus:/etc/prometheus:ro" prom/prometheus:v3.12.0 check rules /etc/prometheus/alerts.yml
PYTHONPATH=backend .venv/bin/python scripts/run_incident_drill.py --output outputs/rollout-readiness/incident-drill-20260713.json
.venv/bin/python scripts/run_functional_acceptance.py --archive /path/to/corpus.zip --corpus-dir outputs/corpus-audit/<latest> --database /private/tmp/spip-v2-functional-acceptance.db --output outputs/functional-acceptance/<run>/report.json
git diff --check
```

## Staging guidance

Do not stage the whole worktree blindly. Review clusters in order, inspect overlap with pre-existing user edits, and stage only after the owner chooses the desired commit structure. A reasonable future commit sequence is persistence → engines → review/governance → rollout/ops → frontend/docs. No file is staged or committed by this manifest.
