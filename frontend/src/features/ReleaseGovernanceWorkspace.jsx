import { Loader2, ShieldCheck, Sparkles } from "lucide-react";
import ShadowComparisonCard from "./ShadowComparisonCard.jsx";
import { EmptyState, Notice } from "./shared/Feedback.jsx";

function metricPercentage(value) {
  return `${Math.round((value || 0) * 1000) / 10}%`;
}

export default function ReleaseGovernanceWorkspace({
  data,
  action,
  reviewerId,
  setReviewerId,
  evaluationForm,
  setEvaluationForm,
  releaseForm,
  setReleaseForm,
  onRefreshShadow,
  onGenerateEvaluation,
  onSaveRelease,
}) {
  const releaseData = data ?? {};
  const releaseSummary = releaseData.summary || {};
  const legacyUsage = releaseSummary.legacy_usage || {};
  const releaseDataset = releaseData.dataset_summary || {};
  const releasePromotion = releaseData.promotion || {};
  const shadowComparison = releaseData.shadow_comparison || {};
  const evaluationReports = releaseData.evaluation_reports || [];
  const authoritativeReports = evaluationReports.filter((item) => item.release_authority);
  const selectedEvaluationReport = evaluationReports.find((item) => item.id === Number(releaseForm.evaluationReportId));
  const selectedReportIsCurrent = Boolean(
    selectedEvaluationReport
    && selectedEvaluationReport.release_authority
    && selectedEvaluationReport.generation_method === "server_derived_v2_partitioned"
    && selectedEvaluationReport.dataset_sha256
    && selectedEvaluationReport.dataset_sha256 === releaseDataset.dataset_sha256
    && Number(selectedEvaluationReport.case_count) === Number(releaseDataset.expert_gold_case_count)
  );
  const requiredReleaseGate = ["shadow", "pilot"].includes(releaseForm.stage) ? "shadow" : "canary";
  const releaseStageReady = Boolean(releasePromotion?.[requiredReleaseGate]?.ready)
    && Boolean(shadowComparison.review_target_reached)
    && (releaseForm.stage !== "general" || Number(releaseDataset.expert_gold_case_count || 0) >= 200);

  return (
    <div className="release-governance-workspace">
      <div className="release-summary-grid">
        <article><strong>{releaseDataset.expert_gold_case_count || 0}</strong><span>Expert-gold aktif</span></article>
        <article><strong>{shadowComparison.completed_count || 0} / 50</strong><span>Shadow comparison</span></article>
        <article><strong>{authoritativeReports.length} / {evaluationReports.length}</strong><span>Report berwenang / total</span></article>
        <article><strong>{releaseSummary.stable_release_cycle_count || 0} / 2</strong><span>Siklus stabil</span></article>
        <article className={legacyUsage.observation_coverage_valid && !legacyUsage.observed_call_count ? "ready" : "blocked"}><strong>{legacyUsage.observed_call_count || 0}</strong><span>Panggilan V1 sejak observasi</span></article>
        <article className={releaseSummary.legacy_deprecation_eligible ? "ready" : "blocked"}><strong>{releaseSummary.legacy_deprecation_eligible ? "Siap" : "Terkunci"}</strong><span>Deprecation V1</span></article>
      </div>
      <Notice tone="info" text="Hanya report server-derived partition-aware yang berwenang membuka gate rilis. Import manual/legacy tetap tersimpan sebagai informasi tetapi tidak memengaruhi promotion readiness. Event yang sudah disimpan tidak dapat diedit atau dihapus." />
      {releaseSummary.legacy_deprecation_reasons?.length ? <Notice tone="warning" text={`Deprecation V1: ${releaseSummary.legacy_deprecation_reasons.join(" · ")}`} /> : null}

      <ShadowComparisonCard report={shadowComparison} refreshing={action === "shadow-refresh"} onRefresh={onRefreshShadow} />

      <article className="release-step-card">
        <div className="release-step-heading"><span>2</span><div><h3>Buat evaluation report dari expert gold</h3><p>Sistem menghitung retrieval, mapping precision, sumber, peran evidence, abstention, grade, latency, dan cost tanpa memasukkan isi dokumen ke report.</p></div></div>
        <div className="release-dataset-status">
          <div><strong>{releaseDataset.expert_gold_case_count || 0} kasus</strong><span>Target shadow 50 · general release 200</span></div>
          <div><strong>{releaseDataset.dataset_sha256 ? "Checksum tersedia" : "Belum ada dataset"}</strong><span>{releaseDataset.dataset_sha256 ? `${releaseDataset.dataset_sha256.slice(0, 16)}…` : "Sahkan kandidat pada tab Dataset Ahli"}</span></div>
        </div>
        <div className="release-form-grid">
          <label>Nama dataset<input value={evaluationForm.datasetName} onChange={(event) => setEvaluationForm((value) => ({...value, datasetName: event.target.value}))} /></label>
          <label>Nama evaluator<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Nama atau email dinas" /></label>
          <label className="release-wide-field">Catatan<textarea rows="2" value={evaluationForm.notes} onChange={(event) => setEvaluationForm((value) => ({...value, notes: event.target.value}))} /></label>
          <label className="governance-attestation release-wide-field"><input type="checkbox" checked={evaluationForm.attested} onChange={(event) => setEvaluationForm((value) => ({...value, attested: event.target.checked}))} /><span>Saya telah memeriksa bahwa kasus berstatus expert gold dan meminta aplikasi menghitung evaluation report dari data aktif.</span></label>
        </div>
        <button className="primary-button" type="button" disabled={Boolean(action) || !releaseDataset.expert_gold_case_count || !reviewerId.trim() || evaluationForm.datasetName.trim().length < 3 || !evaluationForm.attested} onClick={onGenerateEvaluation}>{action === "evaluation-report" ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />}Buat Report Otomatis</button>
        {evaluationReports.length ? (
          <div className="evaluation-report-list">
            {evaluationReports.slice(0, 5).map((report) => (
              <article key={report.id}>
                <div><strong>Report #{report.id} · {report.dataset_name}</strong><span>{report.case_count} kasus · {report.generation_method.replaceAll("_", " ")} · {report.release_authority ? "berwenang untuk rilis" : "informasional"}</span></div>
                <div className="evaluation-metrics">
                  <span>Recall@5 <strong>{metricPercentage(report.metrics?.retrieval_recall_at_5)}</strong></span>
                  <span>Precision@5 <strong>{metricPercentage(report.metrics?.mapping_precision_at_5)}</strong></span>
                  <span>Sumber <strong>{metricPercentage(report.metrics?.source_accuracy)}</strong></span>
                  <span>Peran evidence <strong>{metricPercentage(report.metrics?.evidence_role_accuracy)}</strong></span>
                  <span>Label role <strong>{metricPercentage(report.metrics?.evidence_role_label_coverage)}</strong></span>
                  <span>Abstention <strong>{metricPercentage(report.metrics?.abstention_accuracy)}</strong></span>
                  <span>Template <strong>{metricPercentage(report.metrics?.template_detection_accuracy)}</strong></span>
                  <span>Recall template <strong>{metricPercentage(report.metrics?.template_detection_recall)}</strong></span>
                  <span>Label template <strong>{metricPercentage(report.metrics?.template_label_coverage)}</strong></span>
                  <span>Overgrade <strong>{metricPercentage(report.metrics?.overgrade_rate)}</strong></span>
                  <span>Label grade <strong>{metricPercentage(report.metrics?.grade_label_coverage)}</strong></span>
                  <span>Assessment <strong>{metricPercentage(report.metrics?.grade_assessment_coverage)}</strong></span>
                  <span>Latency <strong>{Math.round((report.metrics?.average_run_latency_seconds || 0) * 10) / 10}s</strong></span>
                  <span>Cost <strong>${Number(report.metrics?.average_estimated_cost_usd || 0).toFixed(4)}</strong></span>
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </article>

      <article className="release-step-card">
        <div className="release-step-heading"><span>3</span><div><h3>Catat keputusan shadow, pilot, atau canary</h3><p>Pilih “passed” hanya setelah comparison selesai. Server menolak keputusan bila 50 shadow pair, kualitas expert, seluruh rule, security, OCR, atau rollback belum memenuhi syarat.</p></div></div>
        <div className="release-form-grid">
          <label>ID siklus<input value={releaseForm.cycleId} onChange={(event) => setReleaseForm((value) => ({...value, cycleId: event.target.value}))} placeholder="Contoh: canary-2026-01" /></label>
          <label>Versi rilis<input value={releaseForm.version} onChange={(event) => setReleaseForm((value) => ({...value, version: event.target.value}))} placeholder="Contoh: 2026.1" /></label>
          <label>Tahap<select value={releaseForm.stage} onChange={(event) => setReleaseForm((value) => ({...value, stage: event.target.value, stableCycle: false}))}><option value="shadow">Shadow</option><option value="pilot">Pilot</option><option value="canary">Canary</option><option value="general">General release</option></select></label>
          <label>Keputusan<select value={releaseForm.decision} onChange={(event) => setReleaseForm((value) => ({...value, decision: event.target.value, stableCycle: false}))}><option value="planned">Direncanakan</option><option value="started">Dimulai</option><option value="passed">Lulus</option><option value="failed">Gagal</option><option value="rolled_back">Rollback</option></select></label>
          <label>Evaluation report<select value={releaseForm.evaluationReportId} onChange={(event) => setReleaseForm((value) => ({...value, evaluationReportId: event.target.value}))}><option value="">Tidak dipilih</option>{evaluationReports.map((report) => { const current = report.release_authority && report.generation_method === "server_derived_v2_partitioned" && report.dataset_sha256 === releaseDataset.dataset_sha256 && Number(report.case_count) === Number(releaseDataset.expert_gold_case_count); return <option value={report.id} disabled={!current} key={report.id}>#{report.id} · {report.dataset_name} · {report.case_count} kasus{report.release_authority ? (current ? "" : " · stale") : " · informasional"}</option>; })}</select></label>
          <label>Ticket/rujukan<input value={releaseForm.ticket} onChange={(event) => setReleaseForm((value) => ({...value, ticket: event.target.value}))} placeholder="Opsional, contoh REL-2026-01" /></label>
          <label>Critical incident<input type="number" min="0" value={releaseForm.criticalIncidentCount} onChange={(event) => setReleaseForm((value) => ({...value, criticalIncidentCount: Number(event.target.value)}))} /></label>
          <label>Nama product owner<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Nama atau email dinas" /></label>
          <label className="release-option"><input type="checkbox" checked={releaseForm.rollbackRehearsed} onChange={(event) => setReleaseForm((value) => ({...value, rollbackRehearsed: event.target.checked}))} /><span>Rollback sudah diuji</span></label>
          <label className="release-option"><input type="checkbox" disabled={releaseForm.decision !== "passed" || !["canary", "general"].includes(releaseForm.stage)} checked={releaseForm.stableCycle} onChange={(event) => setReleaseForm((value) => ({...value, stableCycle: event.target.checked}))} /><span>Tandai sebagai siklus stabil</span></label>
          <label className="release-wide-field">Alasan keputusan<textarea rows="3" value={releaseForm.reason} onChange={(event) => setReleaseForm((value) => ({...value, reason: event.target.value}))} placeholder="Jelaskan hasil comparison, incident, dan keputusan owner" /></label>
          <label className="governance-attestation release-wide-field"><input type="checkbox" checked={releaseForm.attested} onChange={(event) => setReleaseForm((value) => ({...value, attested: event.target.checked}))} /><span>Saya memahami tahap, keputusan, report terpilih, status rollback/incident, dan bahwa event ini menjadi audit trail permanen.</span></label>
        </div>
        {releaseForm.decision === "passed" && !selectedReportIsCurrent ? <Notice tone="warning" text="Keputusan lulus memerlukan report server-derived berwenang yang cocok dengan holdout Evaluasi aktif; report manual/legacy tidak dapat dipakai." /> : null}
        {releaseForm.decision === "passed" && !shadowComparison.review_target_reached ? <Notice tone="warning" text={`Keputusan lulus memerlukan minimal 50 shadow comparison terminal; saat ini ${shadowComparison.completed_count || 0}/50.`} /> : null}
        {releaseForm.decision === "passed" && selectedReportIsCurrent && !releaseStageReady ? <Notice tone="warning" text={`Gate ${requiredReleaseGate} belum siap: ${(releasePromotion?.[requiredReleaseGate]?.reasons || ["threshold tahap belum terpenuhi"]).join(" · ")}`} /> : null}
        <button className="primary-button" type="button" disabled={Boolean(action) || !reviewerId.trim() || releaseForm.cycleId.trim().length < 3 || !releaseForm.version.trim() || releaseForm.reason.trim().length < 8 || !releaseForm.attested || (releaseForm.decision === "passed" && (!selectedReportIsCurrent || !releaseStageReady))} onClick={onSaveRelease}>{action === "release-evidence" ? <Loader2 className="spin" size={17} /> : <ShieldCheck size={17} />}Simpan Event Rilis</button>
      </article>

      <article className="release-history-card">
        <div><h3>Riwayat append-only</h3><span>{releaseData.events?.length || 0} event tercatat</span></div>
        {(releaseData.events || []).length ? <div className="release-event-list">{releaseData.events.slice(0, 20).map((event) => <article key={event.id}><span className={`release-decision ${event.decision}`}>{event.decision.replaceAll("_", " ")}</span><div><strong>{event.release_cycle_id} · {event.stage}</strong><small>Versi {event.release_version} · {event.reviewer_id} · {event.created_at}</small><p>{event.reason}</p></div><div><strong>{event.stable_cycle ? "Stabil" : "Event"}</strong><small>Incident {event.critical_incident_count} · rollback {event.rollback_rehearsed ? "sudah diuji" : "belum"}</small></div></article>)}</div> : <EmptyState text="Belum ada event rilis. Catat planned/started lebih dahulu tanpa mengarang hasil lulus." />}
      </article>
    </div>
  );
}
