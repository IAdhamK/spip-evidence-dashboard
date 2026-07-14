import { useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  FileText,
  Loader2,
  TriangleAlert,
} from "lucide-react";
import { apiPost } from "../../lib/api.js";
import { formatBytes, formatNumber, formatUsdRange } from "../../lib/formatters.js";
import { EmptyState, Notice } from "../shared/Feedback.jsx";
import BatchEvidencePanel from "./SmartUploadBatchPanels.jsx";
import DocumentIntelligenceResult, {
  CrossDocumentPackagePanel,
  V2PackageControl,
  isV2AnalysisResult,
} from "./DocumentIntelligenceResult.jsx";
import { noticeToneForAi } from "./utils.js";


export default function SmartUploadResults({ result }) {
  if (Array.isArray(result?.results)) {
    const isParallel = result.progress_mode === "parallel";
    const pipelineV2 = result.results.some(isV2AnalysisResult);
    return (
      <section className="batch-result-panel">
        <div className="section-heading compact-heading">
          <div>
            <h3>{pipelineV2 ? "Hasil Document Intelligence V2" : isParallel ? "Hasil Analisis Paralel" : "Hasil Analisis Batch"}</h3>
            <p>
              {isParallel
                ? `${result.count} file selesai dianalisis sebagai file individual paralel.`
                : `${result.count} file selesai dianalisis sebagai file individual dan sebagai satu paket evidence.`}
            </p>
          </div>
          <div className="ai-status-box">
            <strong>{isParallel ? "Mode" : "Batch AI"}</strong>
            <span>{isParallel ? "parallel" : result.batch_ai?.status || "skipped"}</span>
          </div>
        </div>
        {!isParallel && result.batch_ai?.message && result.batch_ai.status !== "ok" ? <Notice tone={noticeToneForAi(result.batch_ai.status)} text={result.batch_ai.message} /> : null}
        {!isParallel && result.batch_analysis ? <BatchEvidencePanel analysis={result.batch_analysis} files={result.results.map((item) => item.file)} /> : null}
        {result.package_error ? <Notice tone="warning" text={`Cross-document synthesis: ${result.package_error}`} /> : null}
        {result.package_analysis ? <CrossDocumentPackagePanel analysis={result.package_analysis} /> : null}
        {pipelineV2 && result.results.length >= 2 ? <V2PackageControl runIds={result.results.map((item) => item.run.id)} /> : null}
        <div className="batch-result-list">
          {result.results.map((item, index) => (
            isV2AnalysisResult(item)
              ? <DocumentIntelligenceResult key={item.run?.id || index} result={item} ordinal={index + 1} />
              : <SmartUploadResult key={item.review_id || index} result={item} ordinal={index + 1} />
          ))}
        </div>
      </section>
    );
  }
  return isV2AnalysisResult(result)
    ? <DocumentIntelligenceResult result={result} />
    : <SmartUploadResult result={result} />;
}

function AnalysisSummary({ analysis }) {
  const pageText = analysis.scanned_pages
    ? `${analysis.scanned_pages}${analysis.total_pages ? ` dari ${analysis.total_pages}` : ""} halaman`
    : null;
  const sheetText = analysis.scanned_sheets
    ? `${analysis.scanned_sheets}${analysis.total_sheets ? ` dari ${analysis.total_sheets}` : ""} sheet`
    : null;
  return (
    <section className="analysis-summary" aria-label="Ringkasan konteks AI">
      <div>
        <span>Mode</span>
        <strong>{analysis.label || analysis.mode}</strong>
        <small>{analysis.description}</small>
      </div>
      <div>
        <span>Dipindai</span>
        <strong>{pageText || sheetText || `${formatNumber(analysis.extracted_char_count || 0)} karakter`}</strong>
        <small>{analysis.page_strategy ? `Strategi: ${analysis.page_strategy}` : analysis.method}</small>
      </div>
      <div>
        <span>Dikirim ke AI</span>
        <strong>{formatNumber(analysis.sent_char_count || 0)} karakter</strong>
        <small>{analysis.scanned_text_pages ? `${analysis.scanned_text_pages} halaman berteks` : `Input ~${formatNumber(analysis.estimated_input_tokens || 0)} token`}</small>
      </div>
      <div>
        <span>Kandidat KK</span>
        <strong>{formatNumber(analysis.candidate_count || 0)} / {formatNumber(analysis.candidate_limit || 0)}</strong>
        <small>Pool {formatNumber(analysis.candidate_pool_count || 0)} kandidat</small>
      </div>
      <div>
        <span>Estimasi Biaya</span>
        <strong>{formatUsdRange(analysis.estimated_cost_usd)}</strong>
        <small>Output ~{formatNumber(analysis.estimated_output_tokens || 0)} token</small>
      </div>
    </section>
  );
}

function ReasoningGatePanel({ gate }) {
  const classification = gate?.classification ?? {};
  return (
    <section className="reasoning-gate-panel" aria-label="Reasoning Gate V2.5">
      <div className="reasoning-gate-heading">
        <div>
          <h4>{gate.version || "Reasoning Gate"}</h4>
          <p>{gate.message}</p>
        </div>
        <span>{gate.top_score ? `${Math.round(gate.top_score)}%` : "-"}</span>
      </div>
      <div className="reasoning-gate-grid">
        <div>
          <span>Konteks KK</span>
          <strong>{classification.best_kk || "Belum jelas"}</strong>
          <small>{classification.best_kk_label}</small>
        </div>
        <div>
          <span>Jenis Evidence</span>
          <strong>{classification.evidence_type_label || "Belum terklasifikasi"}</strong>
          <small>Grade aman: {classification.safe_grade_ceiling || "-"}</small>
        </div>
        <div>
          <span>Ambang Utama</span>
          <strong>&gt;{gate.primary_threshold}%</strong>
          <small>{gate.top_status || "Belum ada kandidat"}</small>
        </div>
      </div>
      {gate.grade_rules ? (
        <div className="grade-rule-strip" aria-label="Aturan maturity grade">
          {Object.entries(gate.grade_rules).map(([grade, label]) => (
            <span key={grade}><b>{grade}</b> {label}</span>
          ))}
        </div>
      ) : null}
      {classification.warnings?.length ? (
        <div className="gate-warning-list">
          {classification.warnings.map((warning) => <span key={warning}>{warning}</span>)}
        </div>
      ) : null}
    </section>
  );
}

function ScoreBreakdown({ scorecard }) {
  if (!scorecard) return null;
  const items = [
    ["Konteks KK", scorecard.kk_context],
    ["Subunsur", scorecard.subunsur_match],
    ["Grade", scorecard.grade_match],
    ["Formalitas", scorecard.evidence_strength],
    ["Periode", scorecard.period_match],
  ];
  return (
    <div className="score-breakdown">
      {items.map(([label, value]) => (
        <span key={label}>{label}: <b>{value}</b></span>
      ))}
    </div>
  );
}

function SmartUploadResult({ result, ordinal }) {
  const candidates = result.candidates ?? [];
  const [actionState, setActionState] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState("");
  const uploadAllowed = Boolean(result.upload?.allow_real_upload);
  const actionsEnabled = Boolean(result.upload?.actions_enabled);

  async function performAction(actionType, index = null) {
    setActionState(`${actionType}:${index ?? "review"}`);
    setUploadError("");
    setUploadResult(null);
    try {
      const data = await apiPost("/api/smart-upload/action", {
        review_id: result.review_id,
        candidate_index: index,
        action_type: actionType,
      });
      setUploadResult(data);
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setActionState(null);
    }
  }

  return (
    <section className="smart-result-panel">
      <div className="section-heading compact-heading file-result-heading">
        <div className="file-result-title">
          <span className="file-result-badge">
            <FileText size={16} aria-hidden="true" />
            {ordinal ? `File ${ordinal}` : "File Evidence"}
          </span>
          <div>
            <h3>{ordinal ? `Hasil Rekomendasi #${ordinal}` : "Hasil Rekomendasi"}</h3>
            <p>{result.file?.name} · {formatBytes(result.file?.size_bytes)}</p>
          </div>
        </div>
        <div className="ai-status-box">
          <strong>Status AI</strong>
          <span>{result.ai?.status || "skipped"}</span>
        </div>
      </div>
      {result.extraction ? <Notice tone={result.extraction.status === "ok" ? "info" : "neutral"} text={`Ekstraksi ${result.extraction.method}: ${result.extraction.message || result.extraction.status}`} /> : null}
      {result.extraction?.quality_warning ? <Notice tone="warning" text={result.extraction.quality_warning} /> : null}
      <DuplicateCheckPanel duplicate={result.duplicate_check} />
      {result.analysis ? <AnalysisSummary analysis={result.analysis} /> : null}
      {result.reasoning_gate ? <ReasoningGatePanel gate={result.reasoning_gate} /> : null}
      {result.ai?.message && result.ai.status !== "ok" ? <Notice tone={noticeToneForAi(result.ai.status)} text={result.ai.message} /> : null}
      {result.evidence_analysis ? (
        <EvidenceAnalysisPanel
          analysis={result.evidence_analysis}
          uploadAllowed={uploadAllowed}
          actionsEnabled={actionsEnabled}
          actionState={actionState}
          onAction={performAction}
        />
      ) : null}
      {uploadError ? <Notice tone="danger" text={uploadError} /> : null}
      {uploadResult ? <Notice tone="info" text={`Aksi berhasil: ${uploadResult.message}`} /> : null}
      <div className="candidate-list">
        {candidates.map((candidate, index) => {
          const duplicateBlocksUpload = Boolean(candidate.duplicate_check?.blocks_upload);
          const candidateUploadAllowed = uploadAllowed && candidate.primary_allowed && !duplicateBlocksUpload;
          const candidateUploadTitle = duplicateBlocksUpload
            ? "Upload ditahan karena file serupa sudah terdeteksi di tujuan atau riwayat upload."
            : candidate.primary_allowed
              ? (uploadAllowed ? "Upload file sebagai penempatan utama" : "Upload belum tersedia di konfigurasi server")
              : "Kandidat belum melewati Reasoning Gate >80%";
          return (
          <article className="candidate-card" key={`${candidate.kk_id}-${candidate.detail_kode}-${candidate.grade}-${index}`}>
            <div className="candidate-rank">#{index + 1}</div>
            <div className="candidate-body">
              <div className="candidate-title-row">
                <div>
                  <strong>{candidate.kk_id} / {candidate.kode} / {candidate.detail_kode} · Grade {candidate.grade}</strong>
                  <p>{candidate.subunsur_name}</p>
                </div>
                <div className="candidate-score-stack">
                  <span className={candidate.primary_allowed ? "confidence-pill" : "confidence-pill muted"}>{Math.round(candidate.reasoning_score ?? ((candidate.confidence ?? 0) * 100))}%</span>
                  <small>{candidate.candidate_status || "Belum Dinilai"}</small>
                </div>
              </div>
              <p className="candidate-parameter">{candidate.uraian}</p>
              <div className="candidate-reason">
                {(candidate.reasons ?? []).map((reason) => <span key={reason}>{reason}</span>)}
              </div>
              <ScoreBreakdown scorecard={candidate.reasoning_scorecard} />
              <DuplicateCheckPanel duplicate={candidate.duplicate_check} compact />
              {candidate.gate_warnings?.length ? (
                <div className="gate-warning-list compact">
                  {candidate.gate_warnings.map((warning) => <span key={warning}>{warning}</span>)}
                </div>
              ) : null}
              <div className="candidate-actions">
                <span>{candidate.folder_path}</span>
                <div className="candidate-button-group">
                  {candidate.public_url ? (
                    <a className="row-link-button" href={candidate.public_url} target="_blank" rel="noreferrer">
                      <ExternalLink size={15} />
                      Buka Folder
                    </a>
                  ) : null}
                  <button
                    className="row-action-button"
                    type="button"
                    onClick={() => performAction("upload_primary", index)}
                    disabled={!candidateUploadAllowed || actionState !== null}
                    title={candidateUploadTitle}
                  >
                    {actionState === `upload_primary:${index}` ? <Loader2 className="spin" size={15} /> : <CheckCircle2 size={15} />}
                    Upload Utama
                  </button>
                </div>
              </div>
            </div>
          </article>
          );
        })}
      </div>
      {candidates.length === 0 ? <EmptyState text={result.ai?.status === "ok" ? "Belum ada kandidat yang cukup kuat dari DeepSeek V4." : "DeepSeek V4 belum berhasil merespons, sehingga aplikasi tidak menampilkan rekomendasi lokal."} /> : null}
    </section>
  );
}


function DuplicateCheckPanel({ duplicate, compact = false }) {
  if (!duplicate || duplicate.status === "clear") return null;
  const matches = duplicate.matches ?? [];
  return (
    <div className={`duplicate-panel duplicate-${duplicate.severity || "warning"} ${compact ? "compact" : ""}`}>
      <div className="duplicate-heading">
        {duplicate.severity === "danger" ? <AlertCircle size={17} /> : <TriangleAlert size={17} />}
        <strong>{duplicate.label || "Potensi Duplikat"}</strong>
      </div>
      {duplicate.message ? <p>{duplicate.message}</p> : null}
      {matches.length ? (
        <div className="duplicate-match-list">
          {matches.slice(0, compact ? 2 : 4).map((match, index) => (
            <span key={`${match.source}-${match.remote_path || match.file_name}-${index}`}>
              {duplicateMatchLabel(match)}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function duplicateMatchLabel(match) {
  const location = [match.kk_id, match.kode, match.detail_kode, match.grade ? `Grade ${match.grade}` : ""].filter(Boolean).join(" / ");
  const file = match.file_name || match.remote_path || "file serupa";
  const size = match.size_bytes ? ` · ${formatBytes(match.size_bytes)}` : "";
  return `${location ? `${location}: ` : ""}${file}${size}`;
}

function EvidenceAnalysisPanel({ analysis, uploadAllowed, actionsEnabled, actionState, onAction }) {
  const placements = analysis?.placements ?? {};
  return (
    <section className="evidence-analysis-panel" aria-label="Analisis evidence multi KK">
      <div className="evidence-conclusion-card">
        <span>Kesimpulan Evidence</span>
        <p>{analysis.summary || "DeepSeek belum memberikan kesimpulan evidence yang cukup lengkap."}</p>
      </div>

      <PlacementList
        title="Penempatan Utama"
        description="Lokasi paling kuat untuk file ini jika harus diprioritaskan."
        placements={placements.primary ?? []}
        tone="primary"
        uploadLabel="Upload Utama"
        actionType="upload_primary"
        actionAllowed={uploadAllowed}
        actionState={actionState}
        onAction={onAction}
      />
      <PlacementList
        title="Penempatan Pendukung"
        description="Lokasi yang bisa didukung oleh file yang sama, tetapi bukan fungsi evidence utama."
        placements={placements.supporting ?? []}
        tone="supporting"
        uploadLabel="Rujuk Pendukung"
        actionType="reference_supporting"
        actionAllowed={actionsEnabled}
        actionState={actionState}
        onAction={onAction}
      />
      <PlacementList
        title="Penempatan Lemah / Opsional"
        description="Masih mungkin relevan, tetapi perlu evidence lain agar aman."
        placements={placements.weak ?? []}
        tone="weak"
        uploadLabel="Rujuk Opsional"
        actionType="reference_optional"
        actionAllowed={actionsEnabled}
        actionState={actionState}
        onAction={onAction}
      />

    </section>
  );
}

function PlacementList({ title, description, placements, tone, uploadLabel, actionType, actionAllowed, actionState, onAction }) {
  if (!placements || placements.length === 0) return null;
  return (
    <div className={`placement-section placement-${tone}`}>
      <div className="placement-heading">
        <div>
          <h4>{title}</h4>
          <p>{description}</p>
        </div>
        <span>{placements.length} lokasi</span>
      </div>
      <div className="placement-list">
        {placements.map((placement, index) => {
          const candidateIndex = Number.isInteger(placement.index) ? placement.index : null;
          const stateKey = `${actionType}:${candidateIndex}`;
          const gateAllowed = actionType !== "upload_primary" || placement.primary_allowed !== false;
          const duplicateBlocksUpload = actionType === "upload_primary" && Boolean(placement.duplicate_check?.blocks_upload);
          const buttonAllowed = actionAllowed && gateAllowed && !duplicateBlocksUpload;
          const buttonTitle = duplicateBlocksUpload
            ? "Upload ditahan karena file serupa sudah terdeteksi di tujuan atau riwayat upload."
            : buttonAllowed
              ? `${uploadLabel} untuk folder ini`
              : actionType === "upload_primary" ? "Belum melewati Reasoning Gate >80% atau upload dikunci" : "Aksi belum tersedia";
          return (
            <article className="placement-card" key={`${title}-${placement.kk_id}-${placement.detail_kode}-${placement.grade}-${index}`}>
              <div>
                <strong>{placement.kk_id || "KK"} / {placement.kode || "-"} / {placement.detail_kode || "-"} · Grade {placement.grade || "-"}</strong>
                <p>{placement.subunsur_name}</p>
                {placement.uraian ? <small>{placement.uraian}</small> : null}
              </div>
              <div className="placement-meta">
                {placement.reasoning_score !== null && placement.reasoning_score !== undefined ? <span>{Math.round(placement.reasoning_score)}%</span> : placement.confidence !== null && placement.confidence !== undefined ? <span>{Math.round(placement.confidence * 100)}%</span> : null}
                {placement.candidate_status ? <em>{placement.candidate_status}</em> : placement.role ? <em>{placement.role}</em> : null}
              </div>
              {placement.reason ? <p className="placement-reason">{placement.reason}</p> : null}
              <DuplicateCheckPanel duplicate={placement.duplicate_check} compact />
              <div className="placement-actions">
                <span>{placement.folder_path}</span>
                <div className="candidate-button-group">
                  {placement.public_url ? (
                    <a className="row-link-button" href={placement.public_url} target="_blank" rel="noreferrer">
                      <ExternalLink size={15} />
                      Buka Folder
                    </a>
                  ) : null}
                  {candidateIndex !== null ? (
                    <button
                      className="row-action-button"
                      type="button"
                      onClick={() => onAction(actionType, candidateIndex)}
                      disabled={!buttonAllowed || actionState !== null}
                      title={buttonTitle}
                    >
                      {actionState === stateKey ? <Loader2 className="spin" size={15} /> : <CheckCircle2 size={15} />}
                      {uploadLabel}
                    </button>
                  ) : null}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
