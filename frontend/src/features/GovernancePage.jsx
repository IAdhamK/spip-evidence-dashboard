import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  FileText,
  ListChecks,
  Loader2,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { apiGet, apiPost } from "../lib/api.js";
import { formatSourceLocation } from "../lib/source-location.js";
import ReleaseGovernanceWorkspace from "./ReleaseGovernanceWorkspace.jsx";
import { EmptyState, Notice } from "./shared/Feedback.jsx";

const GOVERNANCE_CHECK_LABELS = {
  feature_enabled: "Feature flag vision aktif",
  provider_flag_validated: "Flag validasi provider aktif",
  api_key_configured: "API key tersedia",
  renderer_available: "Renderer PDF tersedia",
  capability_approved: "Capability disahkan",
  restricted_data_consent_approved: "Consent data restricted aktif",
};

export default function GovernancePage({ onBack }) {
  const [section, setSection] = useState("rules");
  const [rulesData, setRulesData] = useState(null);
  const [ruleSearch, setRuleSearch] = useState("");
  const [ruleFilter, setRuleFilter] = useState("all");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [gradeDecisions, setGradeDecisions] = useState({});
  const [reviewerId, setReviewerId] = useState(() => window.localStorage.getItem("governance-reviewer-id") || "");
  const [ruleReason, setRuleReason] = useState("");
  const [ruleAttested, setRuleAttested] = useState(false);
  const [visionData, setVisionData] = useState(null);
  const [visionReason, setVisionReason] = useState("");
  const [visionAttested, setVisionAttested] = useState(false);
  const [visionExpiry, setVisionExpiry] = useState(365);
  const [expertData, setExpertData] = useState(null);
  const [expertIndex, setExpertIndex] = useState(0);
  const [expertReason, setExpertReason] = useState("");
  const [expertAttested, setExpertAttested] = useState(false);
  const [expertPartition, setExpertPartition] = useState("evaluation");
  const [releaseData, setReleaseData] = useState(null);
  const [evaluationForm, setEvaluationForm] = useState(() => ({
    datasetName: `expert-gold-${new Date().toISOString().slice(0, 10)}`,
    notes: "Evaluasi otomatis dari dataset expert gold aktif.",
    attested: false,
  }));
  const [releaseForm, setReleaseForm] = useState(() => ({
    cycleId: `cycle-${new Date().toISOString().slice(0, 10)}`,
    version: new Date().toISOString().slice(0, 10).replaceAll("-", "."),
    stage: "shadow",
    decision: "planned",
    evaluationReportId: "",
    stableCycle: false,
    rollbackRehearsed: false,
    criticalIncidentCount: 0,
    ticket: "",
    reason: "",
    attested: false,
  }));
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function loadRules(search = ruleSearch, filter = ruleFilter) {
    const query = new URLSearchParams({q: search, review_status: filter, limit: "500"});
    const data = await apiGet(`/api/analysis-runs/governance/rules?${query.toString()}`);
    setRulesData(data);
    setSelectedIndex((current) => Math.min(current, Math.max(0, (data.items?.length || 1) - 1)));
    return data;
  }

  async function loadVision() {
    const data = await apiGet("/api/analysis-runs/governance/vision");
    setVisionData(data);
    return data;
  }

  async function loadExpertDataset() {
    const data = await apiGet("/api/analysis-runs/governance/expert-dataset");
    setExpertData(data);
    setExpertIndex((current) => Math.min(current, Math.max(0, (data.candidates?.length || 1) - 1)));
    return data;
  }

  async function loadReleaseEvidence() {
    const data = await apiGet("/api/analysis-runs/release-evidence");
    setReleaseData(data);
    return data;
  }

  useEffect(() => {
    Promise.all([loadRules("", "all"), loadVision(), loadExpertDataset(), loadReleaseEvidence()])
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setGradeDecisions({});
    setRuleReason("");
    setRuleAttested(false);
  }, [selectedIndex, ruleFilter, ruleSearch]);

  useEffect(() => {
    setExpertReason("");
    setExpertAttested(false);
    setExpertPartition("evaluation");
  }, [expertIndex]);

  async function applyRuleFilter(event) {
    event?.preventDefault();
    setLoading(true);
    setError("");
    try {
      await loadRules(ruleSearch, ruleFilter);
      setSelectedIndex(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function chooseRuleFilter(nextFilter) {
    setRuleFilter(nextFilter);
    setLoading(true);
    setError("");
    try {
      await loadRules(ruleSearch, nextFilter);
      setSelectedIndex(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function saveRuleDecisions() {
    const parameter = rulesData?.items?.[selectedIndex];
    if (!parameter) return;
    const decisions = parameter.rules
      .filter((rule) => ["approved", "rejected"].includes(gradeDecisions[rule.grade]))
      .map((rule) => ({
        kk_id: rule.kk_id,
        kode: rule.kode,
        detail_kode: rule.detail_kode,
        grade: rule.grade,
        rule_checksum: rule.rule_checksum,
        status: gradeDecisions[rule.grade],
      }));
    if (!decisions.length) {
      setError("Pilih keputusan minimal untuk satu grade.");
      return;
    }
    setAction("rules");
    setError("");
    setMessage("");
    try {
      const result = await apiPost("/api/analysis-runs/governance/rules/decisions", {
        reviewer_id: reviewerId.trim(),
        reason: ruleReason.trim(),
        attested: ruleAttested,
        decisions,
      });
      window.localStorage.setItem("governance-reviewer-id", reviewerId.trim());
      await loadRules(ruleSearch, ruleFilter);
      setGradeDecisions({});
      setRuleReason("");
      setRuleAttested(false);
      setMessage(`${result.saved_count} keputusan rule tersimpan dengan riwayat append-only.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  async function runVisionProbe() {
    setAction("probe");
    setError("");
    setMessage("");
    try {
      const result = await apiPost("/api/analysis-runs/governance/vision/probe", {
        reviewer_id: reviewerId.trim(),
      });
      window.localStorage.setItem("governance-reviewer-id", reviewerId.trim());
      await loadVision();
      setMessage(result.probe.status === "passed"
        ? "Uji synthetic lulus. Tidak ada dokumen pengguna yang dikirim."
        : `Uji synthetic gagal: ${result.probe.error_message || "provider belum memenuhi kontrak"}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  async function saveVisionDecision(scope, status) {
    const passedProbe = visionData?.recent_probes?.find((item) => item.status === "passed");
    setAction(`${scope}:${status}`);
    setError("");
    setMessage("");
    try {
      const result = await apiPost("/api/analysis-runs/governance/vision/decisions", {
        reviewer_id: reviewerId.trim(),
        scope,
        status,
        sensitivity_scope: "restricted",
        evidence_sha256: scope === "capability_validation" && status === "approved"
          ? passedProbe?.report_sha256
          : undefined,
        expires_in_days: visionExpiry,
        reason: visionReason.trim(),
        attested: visionAttested,
      });
      window.localStorage.setItem("governance-reviewer-id", reviewerId.trim());
      await loadVision();
      setVisionReason("");
      setVisionAttested(false);
      setMessage(`Keputusan ${scope.replaceAll("_", " ")} disimpan: ${result.decision.status}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  async function saveExpertDecision(decision) {
    const candidate = expertData?.candidates?.[expertIndex];
    if (!candidate) return;
    setAction(`expert:${decision}`);
    setError("");
    setMessage("");
    try {
      const result = await apiPost(`/api/analysis-runs/governance/expert-dataset/${candidate.run_id}/decision`, {
        reviewer_id: reviewerId.trim(),
        decision,
        dataset_partition: expertPartition,
        reason: expertReason.trim(),
        attested: expertAttested,
      });
      window.localStorage.setItem("governance-reviewer-id", reviewerId.trim());
      await loadExpertDataset();
      setExpertReason("");
      setExpertAttested(false);
      setExpertPartition("evaluation");
      setMessage(decision === "approve"
        ? expertPartition === "evaluation"
          ? `Kasus disahkan untuk evaluasi. Dataset holdout sekarang ${result.summary.evaluation_gold_case_count}/50 untuk tahap shadow.`
          : `Kasus disahkan untuk learning. Korpus learning sekarang ${result.summary.learning_gold_case_count} kasus dan tidak dihitung sebagai evaluasi rilis.`
        : "Kasus dikembalikan ke Review Terpandu untuk diperbaiki.");
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  async function generateEvaluationReport() {
    setAction("evaluation-report");
    setError("");
    setMessage("");
    try {
      const result = await apiPost("/api/analysis-runs/evaluation-reports/from-expert-gold", {
        dataset_name: evaluationForm.datasetName.trim(),
        reviewer_id: reviewerId.trim(),
        notes: evaluationForm.notes.trim(),
        attested: evaluationForm.attested,
      });
      window.localStorage.setItem("governance-reviewer-id", reviewerId.trim());
      await loadReleaseEvidence();
      setReleaseForm((value) => ({...value, evaluationReportId: String(result.report.id)}));
      setEvaluationForm((value) => ({...value, attested: false}));
      setMessage(`Evaluation report #${result.report.id} dibuat otomatis dari ${result.report.case_count} expert-gold cases.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  async function refreshShadowComparisons() {
    setAction("shadow-refresh");
    setError("");
    setMessage("");
    try {
      const result = await apiPost("/api/analysis-runs/shadow-comparisons/refresh?limit=500", {});
      await loadReleaseEvidence();
      setMessage(`${result.report.completed_count}/50 shadow comparison terminal sudah tercatat. Checksum report diperbarui otomatis.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  async function saveReleaseEvidence() {
    setAction("release-evidence");
    setError("");
    setMessage("");
    try {
      const result = await apiPost("/api/analysis-runs/release-evidence", {
        release_cycle_id: releaseForm.cycleId.trim(),
        release_version: releaseForm.version.trim(),
        stage: releaseForm.stage,
        decision: releaseForm.decision,
        evaluation_report_id: releaseForm.evaluationReportId ? Number(releaseForm.evaluationReportId) : null,
        stable_cycle: releaseForm.stableCycle,
        rollback_rehearsed: releaseForm.rollbackRehearsed,
        critical_incident_count: Number(releaseForm.criticalIncidentCount || 0),
        reviewer_id: reviewerId.trim(),
        reason: releaseForm.reason.trim(),
        evidence: releaseForm.ticket.trim() ? {ticket: releaseForm.ticket.trim()} : {},
        attested: releaseForm.attested,
      });
      window.localStorage.setItem("governance-reviewer-id", reviewerId.trim());
      await loadReleaseEvidence();
      setReleaseForm((value) => ({...value, reason: "", attested: false}));
      setMessage(`Bukti ${result.event.stage}/${result.event.decision} tersimpan sebagai event #${result.event.id}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  const parameters = rulesData?.items || [];
  const current = parameters[selectedIndex];
  const summary = rulesData?.summary || {};
  const governance = visionData?.governance;
  const localOcr = visionData?.local_ocr;
  const passedProbe = visionData?.recent_probes?.find((item) => item.status === "passed");
  const expertSummary = expertData?.summary || {};
  const retrievalFeedback = expertData?.retrieval_feedback || {};
  const expertCandidate = expertData?.candidates?.[expertIndex];
  if (loading && !rulesData) {
    return <section className="governance-page loading-state"><Loader2 className="spin" size={28} /><span>Memuat governance V2...</span></section>;
  }

  return (
    <section className="governance-page">
      <button className="back-button" type="button" onClick={onBack}><ArrowLeft size={18} />Dashboard</button>
      <div className="governance-header">
        <div>
          <p className="eyebrow">Governance tanpa kode teknis</p>
          <h2>Pengesahan Rule, Dataset Ahli, Bukti Rilis & OCR/Vision</h2>
          <p>Keputusan disimpan dengan identitas, checksum, alasan, versi, masa berlaku, dan riwayat yang tidak ditimpa.</p>
        </div>
        <span>Fail-closed</span>
      </div>
      <div className="governance-tabs">
        <button type="button" className={section === "rules" ? "active" : ""} onClick={() => setSection("rules")}><ListChecks size={17} />Rule Parameter</button>
        <button type="button" className={section === "dataset" ? "active" : ""} onClick={() => setSection("dataset")}><Database size={17} />Dataset Ahli</button>
        <button type="button" className={section === "release" ? "active" : ""} onClick={() => setSection("release")}><Clock3 size={17} />Bukti Rilis</button>
        <button type="button" className={section === "vision" ? "active" : ""} onClick={() => setSection("vision")}><ShieldCheck size={17} />OCR/Vision</button>
      </div>
      {error ? <Notice tone="danger" text={error} /> : null}
      {message ? <Notice tone="info" text={message} /> : null}

      {section === "rules" ? (
        <div className="rule-governance-workspace">
          <div className="governance-summary-grid">
            <button type="button" onClick={() => chooseRuleFilter("pending")}><strong>{summary.pending || 0}</strong><span>Belum diperiksa</span></button>
            <button type="button" onClick={() => chooseRuleFilter("partial")}><strong>{summary.partial || 0}</strong><span>Sebagian</span></button>
            <button type="button" onClick={() => chooseRuleFilter("approved")}><strong>{summary.approved || 0}</strong><span>Disahkan</span></button>
            <button type="button" onClick={() => chooseRuleFilter("rejected")}><strong>{summary.rejected || 0}</strong><span>Ditolak</span></button>
          </div>
          <form className="governance-search" onSubmit={applyRuleFilter}>
            <label><Search size={17} /><input value={ruleSearch} onChange={(event) => setRuleSearch(event.target.value)} placeholder="Cari kode atau uraian parameter" /></label>
            <select value={ruleFilter} onChange={(event) => setRuleFilter(event.target.value)} aria-label="Filter status rule">
              <option value="all">Semua status</option><option value="pending">Belum diperiksa</option><option value="partial">Sebagian</option><option value="approved">Disahkan</option><option value="rejected">Ditolak</option>
            </select>
            <button className="secondary-button" type="submit">Terapkan</button>
          </form>
          {current ? (
            <>
              <div className="governance-parameter-nav">
                <button className="secondary-button" type="button" disabled={selectedIndex === 0} onClick={() => setSelectedIndex((value) => value - 1)}><ChevronLeft size={16} />Sebelumnya</button>
                <select value={selectedIndex} onChange={(event) => setSelectedIndex(Number(event.target.value))} aria-label="Pilih parameter governance">
                  {parameters.map((item, index) => <option key={`${item.kk_id}-${item.detail_kode}`} value={index}>{index + 1}. {item.kk_id}/{item.detail_kode} — {item.uraian}</option>)}
                </select>
                <button className="secondary-button" type="button" disabled={selectedIndex >= parameters.length - 1} onClick={() => setSelectedIndex((value) => value + 1)}>Berikutnya<ChevronRight size={16} /></button>
              </div>
              <article className="governance-parameter-card">
                <div className="governance-parameter-title"><div><span>{current.kk_id} · {current.detail_kode}</span><h3>{current.uraian}</h3></div><strong>{current.approved_grade_count}/5 grade disahkan</strong></div>
                <div className="rule-grade-grid">
                  {current.rules.map((rule) => (
                    <article className={`rule-grade-card ${rule.approval_status}`} key={rule.grade}>
                      <div><strong>Grade {rule.grade}</strong><span>{rule.approval_status}</span></div>
                      <p>{rule.rule_definition.criterion}</p>
                      <dl>
                        <div><dt>Tahap wajib</dt><dd>{(rule.rule_definition.required_stages || []).join(", ") || "-"}</dd></div>
                        <div><dt>Sumber wajib</dt><dd>{(rule.rule_definition.required_source_types || []).join(", ") || "-"}</dd></div>
                        <div><dt>Efektif</dt><dd>{rule.rule_definition.effective_date || "-"}</dd></div>
                        <div><dt>Prerequisite</dt><dd>{rule.rule_definition.prerequisite_grade || "-"}</dd></div>
                        <div><dt>Disqualifier</dt><dd>{(rule.rule_definition.disqualifiers || []).join(", ") || "-"}</dd></div>
                      </dl>
                      <small>Checksum {rule.rule_checksum.slice(0, 12)}…</small>
                      <div className="rule-decision-buttons">
                        <button type="button" className={gradeDecisions[rule.grade] === "approved" ? "active approve" : ""} onClick={() => setGradeDecisions((value) => ({...value, [rule.grade]: "approved"}))}>Setujui</button>
                        <button type="button" className={gradeDecisions[rule.grade] === "rejected" ? "active reject" : ""} onClick={() => setGradeDecisions((value) => ({...value, [rule.grade]: "rejected"}))}>Tolak</button>
                        <button type="button" onClick={() => setGradeDecisions((value) => ({...value, [rule.grade]: "unchanged"}))}>Lewati</button>
                      </div>
                    </article>
                  ))}
                </div>
                <button className="secondary-button" type="button" onClick={() => setGradeDecisions(Object.fromEntries(current.rules.map((rule) => [rule.grade, "approved"])))}>Pilih setujui untuk 5 grade</button>
                <div className="governance-decision-form">
                  <label>Nama/identitas reviewer<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Nama atau email dinas" /></label>
                  <label>Alasan keputusan<textarea value={ruleReason} onChange={(event) => setRuleReason(event.target.value)} placeholder="Jelaskan pemeriksaan terhadap matriks resmi" rows="3" /></label>
                  <label className="governance-attestation"><input type="checkbox" checked={ruleAttested} onChange={(event) => setRuleAttested(event.target.checked)} /><span>Saya telah memeriksa criterion, tahap, sumber, prerequisite, disqualifier, effective date, dan checksum rule yang dipilih.</span></label>
                  <button className="primary-button" type="button" disabled={action === "rules" || !reviewerId.trim() || ruleReason.trim().length < 8 || !ruleAttested} onClick={saveRuleDecisions}>{action === "rules" ? <Loader2 className="spin" size={17} /> : <ShieldCheck size={17} />}Simpan Keputusan Rule</button>
                </div>
              </article>
            </>
          ) : <EmptyState text="Tidak ada parameter yang cocok dengan filter." />}
        </div>
      ) : section === "dataset" ? (
        <div className="expert-governance-workspace">
          <div className="expert-summary-grid">
            <article><strong>{expertSummary.counts?.expert_candidate || 0}</strong><span>Menunggu pengesahan</span></article>
            <article><strong>{expertSummary.expert_gold_case_count || 0} / 50</strong><span>Target shadow</span></article>
            <article><strong>{expertSummary.expert_gold_case_count || 0} / 200</strong><span>Target general release</span></article>
            <article><strong>{expertSummary.learning_gold_case_count || 0}</strong><span>Korpus learning terpisah</span></article>
            <article><strong>{expertSummary.dataset_sha256 ? "Tercatat" : "Belum ada"}</strong><span>Checksum otomatis</span></article>
          </div>
          <Notice tone="info" text="Alurnya sederhana: reviewer pertama membuat kandidat di Review Terpandu, lalu domain owner yang berbeda memeriksa dan mengesahkannya di sini. Aplikasi menghitung checksum otomatis." />
          <Notice
            tone={retrievalFeedback.active ? "info" : "warning"}
            text={retrievalFeedback.active
              ? `Learning retrieval cocok dengan dataset aktif: ${retrievalFeedback.term_count || 0} fingerprint istilah terkontrol dari ${retrievalFeedback.source_label_count || 0} label positif. Learning hanya membantu pencarian parameter dan tidak menentukan grade.`
              : expertSummary.learning_gold_case_count
                ? "Learning retrieval belum cocok dengan checksum dataset aktif dan tidak digunakan. Simpan ulang keputusan dataset ahli untuk menyegarkan registry secara fail-closed."
                : "Learning retrieval menunggu expert gold partisi Learning. Gold Evaluasi, kandidat biasa, dan prediksi mesin tidak pernah dipakai untuk mengajari sistem."}
          />
          {expertCandidate ? (
            <article className="expert-review-card">
              <div className="expert-review-nav">
                <button className="secondary-button" type="button" disabled={expertIndex === 0} onClick={() => setExpertIndex((value) => value - 1)}><ChevronLeft size={16} />Sebelumnya</button>
                <label>Kasus {expertIndex + 1} dari {expertData.candidates.length}
                  <select value={expertIndex} onChange={(event) => setExpertIndex(Number(event.target.value))}>
                    {expertData.candidates.map((item, index) => <option value={index} key={item.id}>{index + 1}. {item.file_name}</option>)}
                  </select>
                </label>
                <button className="secondary-button" type="button" disabled={expertIndex >= expertData.candidates.length - 1} onClick={() => setExpertIndex((value) => value + 1)}>Berikutnya<ChevronRight size={16} /></button>
              </div>
              <div className="expert-review-heading">
                <div><span>Kandidat dari {expertCandidate.reviewer_id}</span><h3>{expertCandidate.file_name}</h3><p>Hasil review: {expertCandidate.outcome.replaceAll("_", " ")} · status template {(expertCandidate.expected_template_status || "not_assessed").replaceAll("_", " ")} · coverage {Math.round(expertCandidate.coverage_percentage || 0)}%</p></div>
                <a className="secondary-button link-button" href={`/api/analysis-runs/guided-review/${expertCandidate.run_id}/document`} target="_blank" rel="noreferrer"><FileText size={17} />Buka Dokumen</a>
              </div>
              <div className="expert-evidence-grid">
                <section><h4>Mapping yang diharapkan</h4>{(expertCandidate.expected_mappings || []).length ? expertCandidate.expected_mappings.map((mapping, index) => <p key={`${mapping.kk_id}-${mapping.detail_kode}-${index}`}><strong>{mapping.kk_id} · {mapping.detail_kode}</strong><span>{mapping.parameter_uraian || "Parameter hasil review"} · Grade {mapping.grade || "belum ditentukan"} · Peran evidence {(mapping.evidence_role || "belum diperiksa").replaceAll("_", " ")}</span></p>) : <p><span>Kasus negatif: bukan evidence.</span></p>}</section>
                <section><h4>Lokasi sumber yang dipilih</h4>{(expertCandidate.expected_source_locations || []).length ? expertCandidate.expected_source_locations.map((source, index) => <p key={`${source.fact_id}-${index}`}><strong>Fakta #{source.fact_id}</strong><span>{formatSourceLocation(source.source_location, source.unit_key)}</span></p>) : <p><span>Tidak memerlukan lokasi sumber untuk kasus negatif.</span></p>}</section>
              </div>
              <div className="governance-decision-form">
                <label>Nama domain owner<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Harus berbeda dari reviewer pertama" /></label>
                <label>Tujuan kasus<select value={expertPartition} onChange={(event) => setExpertPartition(event.target.value)}><option value="evaluation">Evaluasi rilis (holdout)</option><option value="learning">Learning retrieval</option></select><small>Kasus learning tidak dihitung dalam target evaluasi 50/200.</small></label>
                <label>Catatan pemeriksaan<textarea value={expertReason} onChange={(event) => setExpertReason(event.target.value)} placeholder="Contoh: mapping dan lokasi sumber sesuai dokumen" rows="3" /></label>
                <label className="governance-attestation"><input type="checkbox" checked={expertAttested} onChange={(event) => setExpertAttested(event.target.checked)} /><span>Saya sudah membuka dokumen, memeriksa mapping, grade, peran evidence, dan lokasi sumber, serta memahami bahwa partisi Evaluasi tidak boleh dipakai untuk learning dan partisi Learning tidak dihitung sebagai metrik rilis.</span></label>
                <div className="expert-decision-actions">
                  <button className="secondary-button" type="button" disabled={Boolean(action) || !reviewerId.trim() || expertReason.trim().length < 8 || !expertAttested} onClick={() => saveExpertDecision("return")}>Kembalikan untuk Diperbaiki</button>
                  <button className="primary-button" type="button" disabled={Boolean(action) || !reviewerId.trim() || expertReason.trim().length < 8 || !expertAttested} onClick={() => saveExpertDecision("approve")}>{action === "expert:approve" ? <Loader2 className="spin" size={17} /> : <ShieldCheck size={17} />}Sahkan sebagai Expert Gold</button>
                </div>
              </div>
            </article>
          ) : (
            <EmptyState text="Belum ada kandidat yang menunggu. Selesaikan kasus pada halaman Review Terpandu terlebih dahulu." />
          )}
        </div>
      ) : section === "release" ? (
        <ReleaseGovernanceWorkspace
          data={releaseData}
          action={action}
          reviewerId={reviewerId}
          setReviewerId={setReviewerId}
          evaluationForm={evaluationForm}
          setEvaluationForm={setEvaluationForm}
          releaseForm={releaseForm}
          setReleaseForm={setReleaseForm}
          onRefreshShadow={refreshShadowComparisons}
          onGenerateEvaluation={generateEvaluationReport}
          onSaveRelease={saveReleaseEvidence}
        />
      ) : (
        <div className="vision-governance-workspace">
          <div className={`vision-effective-banner ${localOcr?.available || governance?.effective ? "ready" : "blocked"}`}><ShieldCheck size={22} /><div><strong>{localOcr?.available ? `OCR lokal aktif: ${localOcr.provider}` : governance?.effective ? "Vision eksternal efektif" : "OCR/Vision masih fail-closed"}</strong><span>{localOcr?.available ? "Dokumen diproses lokal tanpa transfer data" : `${governance?.provider} · ${governance?.model} · ${governance?.policy_version}`}</span></div></div>
          <article className={`local-ocr-summary ${localOcr?.available ? "ready" : "blocked"}`}>
            <div><span>Jalur utama</span><h3>Local OCR</h3><p>{localOcr?.available ? `${localOcr.provider} tersedia · confidence minimum ${Math.round((localOcr.min_confidence || 0) * 100)}% · batch render ${localOcr.render_batch_units || 1} unit · batas ${localOcr.timeout_seconds}s/attempt, ${localOcr.unit_budget_seconds}s/unit, ${localOcr.document_budget_seconds}s/dokumen.` : "Runtime OCR lokal belum tersedia. Docker menggunakan Tesseract ind+eng; macOS dapat memakai Apple Vision."}</p></div>
            <strong>{localOcr?.external_data_sent ? "Transfer eksternal" : "Data tetap lokal"}</strong>
          </article>
          <div className="vision-check-grid">
            {Object.entries(governance?.checks || {}).map(([key, value]) => <div className={value ? "ready" : "blocked"} key={key}><strong>{value ? "Lulus" : "Belum"}</strong><span>{GOVERNANCE_CHECK_LABELS[key] || key}</span></div>)}
          </div>
          <article className="vision-governance-step">
            <div><span>Fallback eksternal · Langkah 1</span><h3>Uji provider dengan gambar synthetic</h3><p>Hanya gambar buatan bertuliskan “SPIP 2026” yang dikirim. Tidak ada dokumen pengguna.</p></div>
            <button className="secondary-button" type="button" disabled={action === "probe" || !reviewerId.trim()} onClick={runVisionProbe}>{action === "probe" ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />}Jalankan Uji Synthetic</button>
            {visionData?.recent_probes?.[0] ? <div className={`vision-probe-result ${visionData.recent_probes[0].status}`}><strong>{visionData.recent_probes[0].status}</strong><span>{visionData.recent_probes[0].observed_text || visionData.recent_probes[0].error_message}</span><small>{visionData.recent_probes[0].report_sha256}</small></div> : null}
          </article>
          <div className="governance-decision-form vision-decision-form">
            <label>Nama/identitas reviewer<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Nama atau email dinas" /></label>
            <label>Masa berlaku<select value={visionExpiry} onChange={(event) => setVisionExpiry(Number(event.target.value))}><option value="30">30 hari</option><option value="90">90 hari</option><option value="365">1 tahun</option></select></label>
            <label>Alasan keputusan<textarea value={visionReason} onChange={(event) => setVisionReason(event.target.value)} placeholder="Jelaskan dasar capability atau consent" rows="3" /></label>
            <label className="governance-attestation"><input type="checkbox" checked={visionAttested} onChange={(event) => setVisionAttested(event.target.checked)} /><span>Saya memahami scope, provider, model, sensitivitas restricted, masa berlaku, dan bahwa keputusan ini tercatat dalam audit trail.</span></label>
          </div>
          <div className="vision-decision-grid">
            <article><span>Langkah 2</span><h3>Capability teknis</h3><p>Hanya dapat disetujui dari uji synthetic yang lulus pada provider/model aktif.</p><div><button className="primary-button" type="button" disabled={!passedProbe || !visionAttested || visionReason.trim().length < 8 || !reviewerId.trim() || Boolean(action)} onClick={() => saveVisionDecision("capability_validation", "approved")}>Setujui Capability</button>{governance?.capability_decision?.status === "approved" ? <button className="secondary-button" type="button" disabled={Boolean(action)} onClick={() => saveVisionDecision("capability_validation", "revoked")}>Cabut</button> : null}</div></article>
            <article><span>Langkah 3</span><h3>Consent data restricted</h3><p>Consent tidak dapat aktif sebelum capability disahkan dan tidak mengubah feature flag server.</p><div><button className="primary-button" type="button" disabled={!governance?.checks?.capability_approved || !visionAttested || visionReason.trim().length < 8 || !reviewerId.trim() || Boolean(action)} onClick={() => saveVisionDecision("external_data_processing", "approved")}>Setujui Consent</button>{governance?.data_processing_decision?.status === "approved" ? <button className="secondary-button" type="button" disabled={Boolean(action)} onClick={() => saveVisionDecision("external_data_processing", "revoked")}>Cabut</button> : null}</div></article>
          </div>
          <Notice tone="warning" text="Local OCR tidak membutuhkan consent eksternal. Approval UI dan environment flag hanya mengatur fallback vision Sumopod; fallback tersebut tetap terkunci sampai seluruh gate lulus." />
        </div>
      )}
    </section>
  );
}
