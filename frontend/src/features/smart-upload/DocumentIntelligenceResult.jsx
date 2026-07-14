import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { apiGet, apiPost } from "../../lib/api.js";
import { formatSourceLocation } from "../../lib/source-location.js";
import { EmptyState } from "../shared/Feedback.jsx";
import AdministrativeResultView from "./AdministrativeResultView.jsx";
import ControlledUploadState from "./ControlledUploadState.jsx";
import { primaryAdministrativeResult } from "./admin-result.js";

export { CrossDocumentPackagePanel, V2PackageControl } from "./DocumentPackagePanels.jsx";

export function isV2AnalysisResult(value) {
  return Boolean(value?.run?.pipeline_version && Array.isArray(value?.engines));
}

function ComputeRoutingTrace({ engine }) {
  if (!engine.engine_name?.startsWith("compute_routing_")) return null;
  const decision = engine.output ?? {};
  return (
    <p>
      Route: {(decision.target || "deterministik").replaceAll("_", " ")} · {decision.selected ? "dipilih" : "tidak dipilih"}
      <br />Skor kompleksitas {Math.round((decision.complexity_score ?? 0) * 100)} · risiko {Math.round((decision.risk_score ?? 0) * 100)}
    </p>
  );
}

export default function DocumentIntelligenceResult({ result, ordinal }) {
  const [snapshot, setSnapshot] = useState(result);
  const [viewMode, setViewMode] = useState("simple");
  const [reviewIntent, setReviewIntent] = useState(null);
  const [reviewerId, setReviewerId] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [correctionTarget, setCorrectionTarget] = useState("");
  const [correctionGrade, setCorrectionGrade] = useState("");
  const [correctionCatalog, setCorrectionCatalog] = useState([]);
  const [correctionCatalogLoading, setCorrectionCatalogLoading] = useState(false);
  const [correctionCatalogError, setCorrectionCatalogError] = useState("");
  const [reviewAction, setReviewAction] = useState("");
  const [reviewMessage, setReviewMessage] = useState("");
  const [reconciliationOutcome, setReconciliationOutcome] = useState("needs_investigation");
  const [reconciliationAttested, setReconciliationAttested] = useState(false);
  useEffect(() => setSnapshot(result), [result]);
  const run = snapshot.run ?? {};
  const engines = snapshot.engines ?? [];
  const facts = snapshot.facts ?? [];
  const mappings = snapshot.mappings ?? [];
  const assessments = new Map((snapshot.grade_assessments ?? []).map((item) => [item.mapping_candidate_id, item]));
  const verifications = (snapshot.verification_results ?? []).reduce((items, item) => {
    items.set(item.mapping_candidate_id, [...(items.get(item.mapping_candidate_id) ?? []), item]);
    return items;
  }, new Map());
  const uploadActions = (snapshot.controlled_upload_actions ?? []).reduce(
    (items, item) => items.set(item.mapping_candidate_id, item),
    new Map(),
  );
  const uploadReconciliations = (snapshot.controlled_upload_reconciliations ?? []).reduce(
    (items, item) => items.set(item.action_id, item),
    new Map(),
  );
  const primaryResult = primaryAdministrativeResult({
    mappings,
    assessments: snapshot.grade_assessments ?? [],
    verificationResults: snapshot.verification_results ?? [],
  });

  async function refreshRun() {
    const fresh = await apiGet(`/api/analysis-runs/${run.id}`);
    setSnapshot(fresh);
  }

  async function loadCorrectionCatalog(force = false) {
    if (!force && (correctionCatalog.length || correctionCatalogLoading)) return;
    if (force) setCorrectionCatalog([]);
    setCorrectionCatalogLoading(true);
    setCorrectionCatalogError("");
    try {
      const catalog = await apiGet("/api/analysis-runs/parameter-catalog?limit=1000");
      const items = Array.isArray(catalog.items) ? catalog.items : [];
      if (!items.length || items.length < Number(catalog.parameter_count || 0)) {
        throw new Error("Katalog parameter belum termuat lengkap.");
      }
      setCorrectionCatalog(items);
    } catch (error) {
      setCorrectionCatalogError(error.message || "Katalog parameter gagal dimuat.");
    } finally {
      setCorrectionCatalogLoading(false);
    }
  }

  async function decide(mappingId, decision) {
    if (reviewerId.trim().length < 2 || reviewReason.trim().length < 8) {
      setReviewMessage("Isi nama pemeriksa dan catatan pemeriksaan minimal 8 karakter.");
      return false;
    }
    let finalMapping = {};
    if (decision === "correct") {
      const [kkId, kode, detailKode] = correctionTarget.split("|").map((item) => item.trim());
      if (!kkId || !kode || !detailKode) {
        setReviewMessage("Pilih parameter yang benar untuk menyimpan koreksi.");
        return false;
      }
      finalMapping = { kk_id: kkId, kode, detail_kode: detailKode, grade: correctionGrade || null };
    }
    setReviewAction(`${decision}:${mappingId}`);
    setReviewMessage("");
    try {
      await apiPost(`/api/analysis-runs/${run.id}/review-decisions`, {
        reviewer_id: reviewerId.trim(),
        decision,
        mapping_candidate_id: mappingId,
        final_mapping: finalMapping,
        reason: reviewReason.trim(),
      });
      await refreshRun();
      setReviewMessage("Keputusan pemeriksaan berhasil disimpan.");
      return true;
    } catch (error) {
      setReviewMessage(error.message);
      return false;
    } finally {
      setReviewAction("");
    }
  }

  function beginSimpleReview(mapping, decision) {
    setReviewIntent({ mappingId: mapping.id, decision });
    setCorrectionTarget(`${mapping.kk_id}|${mapping.kode}|${mapping.detail_kode}`);
    setCorrectionGrade(assessments.get(mapping.id)?.candidate_grade || "");
    if (decision === "correct") loadCorrectionCatalog();
    if (!reviewReason.trim()) {
      setReviewReason({
        approve: "Hasil analisis sesuai dengan isi dokumen.",
        correct: "Hasil analisis perlu diperbaiki sesuai pemeriksaan dokumen.",
        reject: "Dokumen ini bukan evidence yang sesuai.",
      }[decision]);
    }
    setReviewMessage("");
  }

  async function submitSimpleReview() {
    if (!reviewIntent) return;
    const saved = await decide(reviewIntent.mappingId, reviewIntent.decision);
    if (saved) setReviewIntent(null);
  }

  async function controlledUpload(mappingId) {
    if (reviewerId.trim().length < 2) {
      setReviewMessage("Isi identitas reviewer sebelum controlled upload.");
      return;
    }
    setReviewAction(`upload:${mappingId}`);
    setReviewMessage("");
    try {
      const upload = await apiPost(`/api/analysis-runs/${run.id}/approve-upload`, {
        reviewer_id: reviewerId.trim(),
        mapping_candidate_id: mappingId,
        category_name: categoryName.trim() || null,
      });
      await refreshRun();
      setReviewMessage(upload.idempotent
        ? "Upload ini sudah berhasil sebelumnya; audit action yang sama digunakan tanpa upload ulang."
        : "Controlled upload berhasil dan audit action tersimpan.");
    } catch (error) {
      setReviewMessage(error.message);
    } finally {
      setReviewAction("");
    }
  }

  async function reconcileUpload(action) {
    if (reviewerId.trim().length < 2 || reviewReason.trim().length < 8) {
      setReviewMessage("Isi identitas reviewer dan alasan pemeriksaan minimal 8 karakter.");
      return;
    }
    if (!reconciliationAttested) {
      setReviewMessage("Centang pernyataan pemeriksaan folder tujuan dan legacy review.");
      return;
    }
    const reconciliation = uploadReconciliations.get(action.id);
    setReviewAction(`reconcile:${action.id}`);
    setReviewMessage("");
    try {
      const saved = await apiPost(`/api/analysis-runs/${run.id}/controlled-upload-actions/${action.id}/reconciliation`, {
        reviewer_id: reviewerId.trim(),
        outcome: reconciliationOutcome,
        reason: reviewReason.trim(),
        attested: true,
        expected_latest_event_id: reconciliation?.latest_event_id ?? null,
      });
      await refreshRun();
      setReconciliationAttested(false);
      setReviewMessage(saved.reconciliation?.effective
        ? "Rekonsiliasi final: dua reviewer berbeda memberikan hasil yang sama."
        : "Pemeriksaan tersimpan. Masih diperlukan reviewer kedua yang independen dengan hasil yang sama.");
    } catch (error) {
      setReviewMessage(error.message);
    } finally {
      setReviewAction("");
    }
  }

  async function expandCandidates() {
    setReviewAction("expand");
    setReviewMessage("");
    try {
      const expanded = await apiPost(`/api/analysis-runs/${run.id}/expand-candidates`, { limit: 30 });
      setSnapshot(expanded);
      setReviewMessage(`${expanded.candidate_expansion?.new_candidate_count ?? 0} kandidat baru ditambahkan; seluruh hasil wajib direview ulang.`);
    } catch (error) {
      setReviewMessage(error.message);
    } finally {
      setReviewAction("");
    }
  }

  async function retryRun() {
    setReviewAction("retry");
    setReviewMessage("");
    try {
      const retried = await apiPost(`/api/analysis-runs/${run.id}/retry`, {});
      setReviewMessage(`Retry job ${retried.job?.id || "baru"} masuk antrean dari run #${run.id}.`);
    } catch (error) {
      setReviewMessage(error.message);
    } finally {
      setReviewAction("");
    }
  }
  return (
    <section className="smart-result-panel intelligence-v2-result">
      <div className="section-heading compact-heading file-result-heading">
        <div className="file-result-title">
          <span className="file-result-badge"><FileText size={16} />{ordinal ? `Dokumen ${ordinal}` : "Hasil Analisis Dokumen"}</span>
          <div>
            <h3>{run.file_name || "Dokumen yang dianalisis"}</h3>
            <p>{viewMode === "simple" ? "Periksa rekomendasi sistem sebelum menyimpan hasil." : `Run #${run.id} · ${run.pipeline_version} · ${run.analysis_mode}`}</p>
          </div>
        </div>
        <div className="v2-view-switch" aria-label="Pilihan tampilan hasil">
          <button type="button" className={viewMode === "simple" ? "active" : ""} onClick={() => setViewMode("simple")}>Ringkasan</button>
          <button type="button" className={viewMode === "detail" ? "active" : ""} onClick={() => setViewMode("detail")}>Detail Pemeriksaan</button>
        </div>
      </div>

      {viewMode === "simple" ? (
        <AdministrativeResultView
          run={run}
          primaryResult={primaryResult}
          mappings={mappings}
          reviewIntent={reviewIntent}
          reviewerId={reviewerId}
          setReviewerId={setReviewerId}
          reviewReason={reviewReason}
          setReviewReason={setReviewReason}
          correctionTarget={correctionTarget}
          setCorrectionTarget={setCorrectionTarget}
          correctionGrade={correctionGrade}
          setCorrectionGrade={setCorrectionGrade}
          correctionCatalog={correctionCatalog}
          correctionCatalogLoading={correctionCatalogLoading}
          correctionCatalogError={correctionCatalogError}
          reloadCorrectionCatalog={() => loadCorrectionCatalog(true)}
          reviewAction={reviewAction}
          reviewMessage={reviewMessage}
          beginReview={beginSimpleReview}
          submitReview={submitSimpleReview}
          cancelReview={() => setReviewIntent(null)}
        />
      ) : (
      <>
      <section className="analysis-summary" aria-label="Coverage Document Intelligence">
        <div><span>Coverage</span><strong>{Math.round(run.coverage_percentage ?? 0)}%</strong><small>{run.processed_units ?? 0}/{run.total_units ?? 0} unit</small></div>
        <div><span>Status Coverage</span><strong>{run.coverage_status || "unknown"}</strong><small>{run.ocr_required_units ?? 0} perlu OCR · {run.failed_units ?? 0} gagal</small></div>
        <div><span>Fakta Bersumber</span><strong>{facts.length}</strong><small>Setiap fakta terhubung ke unit sumber</small></div>
        <div><span>Kandidat Parameter</span><strong>{mappings.length}</strong><small>Retrieval parameter-first tanpa memilih grade</small></div>
        <div><span>Primary Upload</span><strong>{run.primary_blocked ? "Diblokir" : "Diizinkan"}</strong><small>Coverage + rule + verification + human review</small></div>
      </section>

      {run.block_reasons?.length ? (
        <div className="gate-warning-list">
          {run.block_reasons.map((reason) => <span key={reason}>{reason}</span>)}
        </div>
      ) : null}

      <section className="engine-trace-panel">
        <div className="placement-heading"><div><h4>Execution Trace Engine</h4><p>Status aktual dari backend, bukan progress timer simulasi.</p></div><span>{engines.length} engine</span></div>
        <div className="engine-trace-grid">
          {engines.map((engine) => (
            <article key={`${engine.engine_name}-${engine.id}`} className={`engine-trace-card ${engine.status}`}>
              <strong>{engine.engine_name.replaceAll("_", " ")}</strong>
              <span>{engine.status}</span>
              <small>{engine.coverage?.processed ?? 0}/{engine.coverage?.required ?? 0} diproses · {engine.metrics?.duration_ms ?? 0} ms</small>
              <ComputeRoutingTrace engine={engine} />
              {engine.warnings?.slice(0, 2).map((warning) => <p key={warning}>{warning}</p>)}
            </article>
          ))}
        </div>
      </section>

      {facts.length ? (
        <section className="v2-fact-panel">
          <div className="placement-heading"><div><h4>Fakta dan Sumber</h4><p>Klaim atomik hasil Fact Extraction Engine.</p></div><span>{facts.length} fakta</span></div>
          <div className="v2-fact-list">
            {facts.slice(0, 20).map((fact) => {
              const source = fact.sources?.[0];
              return (
                <article key={fact.id}>
                  <div><strong>{fact.fact_type}</strong><span>{fact.period || "periode belum terbaca"} · peran {(fact.evidence_role || "context").replaceAll("_", " ")} (advisory)</span></div>
                  <p>{fact.claim}</p>
                  <small>{formatSourceLocation(source?.source_location, source?.unit_key)}</small>
                </article>
              );
            })}
          </div>
        </section>
      ) : null}

      <section className="v2-mapping-panel">
        <div className="placement-heading">
          <div><h4>Kandidat Mapping Parameter</h4><p>Grade dihitung setelah mapping; rule draft tidak dapat mengaktifkan upload.</p></div>
          <div className="v2-candidate-controls">
            <span>{mappings.length} kandidat</span>
            {run.analysis_mode === "full_audit" && run.coverage_status === "complete" ? (
              <button className="row-action-button" type="button" disabled={Boolean(reviewAction)} onClick={expandCandidates}>Perluas Kandidat</button>
            ) : null}
            {["failed", "cancelled", "blocked"].includes(run.status) ? (
              <button className="row-action-button" type="button" disabled={Boolean(reviewAction)} onClick={retryRun}>Retry Run</button>
            ) : null}
          </div>
        </div>
        <div className="placement-list">
          {mappings.map((mapping) => {
            const grade = assessments.get(mapping.id);
            const mappingVerifications = verifications.get(mapping.id) ?? [];
            const verificationStatus = mappingVerifications.length && mappingVerifications.every((item) => item.status === "verified")
              ? "verified"
              : mappingVerifications.some((item) => item.status === "needs_human_review") ? "needs_human_review" : "belum diverifikasi";
            const uploadAction = uploadActions.get(mapping.id);
            const uploadStatus = uploadAction?.status;
            const uploadReconciliation = uploadAction
              ? uploadReconciliations.get(uploadAction.id)
              : null;
            const uploadLocked = ["uploading", "uploaded_primary", "blocked_ambiguous"].includes(uploadStatus);
            const uploadLabel = uploadStatus === "uploading"
              ? "Sedang diproses"
              : uploadStatus === "uploaded_primary"
                ? "Sudah diupload"
                : uploadStatus === "blocked_ambiguous"
                  ? "Perlu verifikasi operator"
                  : "Controlled Upload";
            return (
              <article className="placement-card" key={mapping.id}>
                <div><strong>{mapping.kk_id} / {mapping.kode} / {mapping.detail_kode}</strong><p>Skor kecocokan {Math.round((mapping.mapping_score ?? 0) * 100)}%{mapping.rag_rank ? ` · Peringkat RAG ${mapping.rag_rank}` : ""}</p></div>
                <div className="placement-meta"><span>Grade {grade?.candidate_grade || "-"}</span><em>{verificationStatus}</em></div>
                {mapping.rag_method ? <p className="placement-reason">Metode pencarian: {mapping.rag_method === "deepseek_v4_pro_constrained_rerank_v1" ? "Advanced RAG + DeepSeek V4 Pro (reranking terbatas)" : "Advanced RAG lokal"}. Grade tetap dihitung oleh rule engine.</p> : null}
                {mapping.reasons?.map((reason) => <p className="placement-reason" key={reason}>{reason}</p>)}
                {mappingVerifications.some((item) => item.findings?.length) ? <div className="gate-warning-list compact">{mappingVerifications.flatMap((item) => item.findings ?? []).map((finding, index) => <span key={`${finding}-${index}`}>{finding}</span>)}</div> : null}
                <ControlledUploadState
                  action={uploadAction}
                  reconciliation={uploadReconciliation}
                  busy={Boolean(reviewAction)}
                  onRefresh={refreshRun}
                  onReconcile={reconcileUpload}
                />
                <div className="v2-review-actions">
                  <button className="row-action-button" type="button" disabled={Boolean(reviewAction)} onClick={() => decide(mapping.id, "approve")}>Setujui</button>
                  <button className="row-action-button" type="button" disabled={Boolean(reviewAction)} onClick={() => decide(mapping.id, "correct")}>Koreksi</button>
                  <button className="row-action-button" type="button" disabled={Boolean(reviewAction)} onClick={() => decide(mapping.id, "reject")}>Tolak</button>
                  {!run.primary_blocked ? <button className="row-action-button" type="button" disabled={Boolean(reviewAction) || uploadLocked} onClick={() => controlledUpload(mapping.id)}>{uploadLabel}</button> : null}
                </div>
              </article>
            );
          })}
          {mappings.length === 0 ? <EmptyState text="Retrieval Engine abstain: belum ada parameter yang didukung fakta bersumber." /> : null}
        </div>
        <div className="v2-review-form">
          <label>Identitas reviewer<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="ID/email dari identity provider" /></label>
          <label>Alasan keputusan<textarea value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="Jelaskan dasar approve atau reject" /></label>
          <label>Target koreksi<input value={correctionTarget} onChange={(event) => setCorrectionTarget(event.target.value)} placeholder="KK3.1|1.1|1.1.1" /></label>
          <label>Grade koreksi<select value={correctionGrade} onChange={(event) => setCorrectionGrade(event.target.value)}><option value="">Belum ditetapkan</option>{['E', 'D', 'C', 'B', 'A'].map((grade) => <option value={grade} key={grade}>Grade {grade}</option>)}</select></label>
          <label>Kategori folder (jika tujuan lebih dari satu)<input value={categoryName} onChange={(event) => setCategoryName(event.target.value)} placeholder="Contoh: Evidence Utama" /></label>
          <label>Hasil pemeriksaan upload<select value={reconciliationOutcome} onChange={(event) => setReconciliationOutcome(event.target.value)}><option value="needs_investigation">Masih perlu investigasi</option><option value="confirmed_uploaded">Sudah ada di folder tujuan</option><option value="confirmed_not_uploaded">Tidak ada di folder tujuan</option></select></label>
          <label className="reconciliation-attestation"><input type="checkbox" checked={reconciliationAttested} onChange={(event) => setReconciliationAttested(event.target.checked)} />Saya sudah memeriksa folder tujuan dan legacy review secara langsung.</label>
          <small>Hasil hanya menjadi final setelah dua reviewer berbeda mengirim keputusan yang sama. Sistem tidak akan mengupload ulang otomatis.</small>
          {reviewMessage ? <small>{reviewMessage}</small> : null}
        </div>
      </section>
      </>
      )}
    </section>
  );
}
