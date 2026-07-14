# ADR-001: Pipeline Document Intelligence V2

Status: accepted for development, production activation pending rollout gates
Date: 12 July 2026

## Decision

The application uses a versioned multi-engine pipeline rather than one prompt or one model. Intake/security, routing, native parsing, selective OCR, document mapping, coverage, template detection, document-family classification, fact extraction, family-scoped parameter retrieval, mapping, grade eligibility, deterministic grade rules, independent verification, cross-document synthesis, human review, and controlled upload remain separate auditable stages.

V1 and V2 coexist behind feature flags. V2 cannot influence the production decision while shadow mode is active. SQLite remains the initial durable store and single-replica queue. Atomic job claim/lease is tested across concurrent connections; Migration V21 adds a singleton worker-leader lease with heartbeat and crash reclaim so a second manager cannot start accidentally against the same database. The worker refuses `ANALYSIS_EXPECTED_REPLICAS>1` and rejects external queue names while canonical state remains local. A multi-instance deployment must move canonical persistence and queue coordination together to a shared backend before increasing replicas.

V2 may call the legacy WebDAV transport only through `app.analysis.legacy_bridge`, after coverage, rule, verification, and human approval gates pass. Shadow comparison reads the legacy candidate contract through the same bridge. V2 domain engines do not import the legacy recommendation service.

Migration V22 records each legacy shadow review with its V2 job/run. The comparison report is deterministic and content-free, exposes agreement metrics only, and has no quality or decision authority. A release event marked `passed` requires at least 50 terminal shadow pairs and a separate current expert-gold evaluation; V1/V2 agreement cannot substitute for domain truth.

Migration V23 records content-free daily usage counts for every legacy recommendation/action entrypoint and the V2 controlled-upload bridge. V1 deprecation eligibility requires two validated stable cycles, rollback evidence, telemetry coverage beginning no later than the first observed stable cycle, and zero legacy calls during that observation window. File names, document content, reviewer identity, and payloads are never stored in this telemetry.

Migration V24 records immutable retrieval-feedback snapshots derived only from active two-person expert-gold labels. Compilation requires repeated, sufficiently precise mappings, excludes organization/period and official-corpus tokens, and binds every snapshot to both expert-dataset and parameter-catalog checksums. Persisted terms are one-way SHA-256 fingerprints rather than readable document vocabulary. A stale or failed refresh deactivates feedback; the base hybrid retriever remains available. Feedback may improve parameter recall through a bounded bonus, but it has no grade, verification, approval, or release authority.

Migration V25 separates two-person expert gold into mutually exclusive `evaluation` and `learning` partitions. Existing labels default to evaluation and every pre-partition feedback snapshot is deactivated during upgrade. Release metrics and the 50/200 thresholds use evaluation only; feedback compilation uses learning only. The same document checksum cannot be approved into both partitions, and any pre-existing overlap blocks evaluation and deactivates learning. This prevents train/evaluation leakage and preserves evaluation as a holdout set.

Migration V26 makes evaluation-report release authority explicit and immutable. Existing and manually imported reports are informational. Only the partition-aware server generator may create an authoritative report. Promotion ignores informational reports, and a passed release event recomputes the evaluation then verifies report hash, dataset checksum, case count, generation method, and authority. Historical stable-cycle evidence without the V26 authority/recompute fields remains auditable but is excluded from stable-cycle and V1-deprecation counts.

Migration V29 stores an evidence role and derivation provenance on every extracted fact. Deterministic classification remains the baseline; a structured-model role is advisory, cannot authorize grade or upload, and is visible to the reviewer. Positive expert mappings must carry a reviewed role before they can become expert gold.

Migration V30 stores the reviewer expectation that a document is substantive or template-only. Historical labels remain `not_assessed` and cannot be promoted without review. Authoritative evaluation reports compare this label with the template-completeness ledger, measure accuracy and empty-template recall, and require both label coverage and empty-template recall of at least 95% for promotion. This prevents a corpus with no reviewed empty templates from silently satisfying the template-quality gate.

Migration V32 records the deterministic Document Family Gate output on mapping and grade assessment artefacts. `DocumentFamilyEngine` runs after coverage/template detection and before retrieval. Its audited registry hard-restricts compatible parameter keys; incompatible parameters are removed rather than merely score-demoted. Adaptive XLSX screening inventories every sheet and prioritizes structurally relevant risk-matrix sheets even outside the first four. Document evidence role is inherited by unit/fact authority, so a transmittal letter cannot turn an evaluation phrase into primary evidence. Calibrated decision confidence is stored separately from retrieval and mapping scores. Grade eligibility blocks low relevant coverage, low family confidence, ambiguity, unknown family, and non-grade document families before domain rules run.

Compute routing is a deterministic engine, not a user-mode alias. Policy `compute-routing-v1` records format/structure/coverage/ambiguity/risk factors and selects optional structured extraction, constrained mapping review, or second-pass verification. `screening/full_audit` is retained as provenance but cannot be the sole model-selection input. Structured extraction receives only eligible units left uncovered by deterministic facts. Mapping reasoning is candidate-constrained and demotion-only: it cannot create parameter IDs, increase scores, promote `needs_review`, or determine grade. Model verification is called only for high-risk candidates already accepted by the deterministic verifier and remains veto-only. Provider failure, missing response, or invalid key holds affected mapping for human review; model findings text is not persisted by the mapping routing engine.

Parameter retrieval uses `advanced-rag-v1`: filename-aware BM25, cosine-IDF, conservative SPIP/administrative semantic vectors, expert-gold feedback, reciprocal-rank fusion, and diversity limits across repeated KK/subunsur variants. Grade criteria are excluded from the retrieval corpus. When external AI is allowed, DeepSeek V4 Pro searches a compact representation of the complete official KK–subunsur–parameter catalog through Chat Completions, and its shortlist is validated against exact catalog keys before fusion. Bounded query paraphrases are only a fallback when catalog search returns no valid item. The same constrained adapter may rerank up to ten known candidate keys and demote doubtful candidates. It cannot create a parameter, change `mapping_score`, promote status, select a Grade, bypass verification, or authorize upload. A provider outage falls back to local retrieval and fail-closed review.

## Safety invariants

- A model cannot create source locations or override a deterministic rejection.
- Partial coverage, OCR failure, stale rule checksum, missing verification, or missing human approval blocks primary upload.
- Rule approval is bound to parameter, grade, rule version, and checksum.
- Cross-document evidence is grouped by parameter, organization, and period; contradictions block synthesis approval.
- Real upload reuses duplicate checks and requires `SMART_UPLOAD_ALLOW_REAL_UPLOAD=true`.
- V2 defaults off; rollback is a flag change, not destructive migration.
- Candidate labels, evaluation gold, and model predictions cannot train retrieval; only current two-person learning gold can create an active feedback snapshot.
- Complexity/risk scores are routing scores, not calibrated confidence. They have compute-selection authority only and cannot authorize mapping, grade, upload, or release.
- Evidence role from the structured provider is advisory; only a reviewed expert role contributes to evaluation, and it cannot authorize grade or upload.
- A legacy `not_assessed` template label and an evaluation corpus without reviewed empty-template cases both block promotion.
- A mapping demoted by constrained reasoning cannot be restored by the deterministic or model verifier; promotion requires a new human-reviewed artefact/run.
- A family-incompatible parameter cannot enter retrieval, candidate expansion, grading, or upload even when an external model recommends it.
- A transmittal letter, meeting invitation, photo, or template has no standalone Grade; unknown family remains exploratory and Grade-blocked.

## Consequences

The design has more tables and stages, but every decision is replayable and independently testable. Provider changes remain optional adapters. General release is not allowed until expert gold evaluation and the pilot gates in the operations runbook are signed off.
