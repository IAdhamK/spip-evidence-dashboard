import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Loader2,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { apiGet, apiPost, apiUpload } from "../lib/api.js";
import { formatBytes } from "../lib/formatters.js";
import OperationalReadinessPanel from "./OperationalReadinessPanel.jsx";
import BatchZipIntakePanel from "./smart-upload/BatchZipIntakePanel.jsx";
import {
  AnalysisModePicker,
  CandidateLimitControl,
  SmartUploadProgressBoard,
} from "./smart-upload/SmartUploadControls.jsx";
import LinkCrawlPanel, { aggregateLinkCrawlFromResult } from "./smart-upload/SmartUploadLinkPanel.jsx";
import SmartUploadResults from "./smart-upload/SmartUploadResults.jsx";
import { noticeToneForAi } from "./smart-upload/utils.js";
import { Notice } from "./shared/Feedback.jsx";
import { StatusPill } from "./shared/StatusPill.jsx";

const SMART_UPLOAD_PARALLEL_LIMITS = {
  fast: 4,
  deep: 3,
  full: 2,
};

function smartUploadJobId(file, index) {
  return `${index}-${file.name || "file"}-${file.size || 0}-${file.lastModified || 0}`;
}

function makeSmartUploadJobs(files) {
  return files.map((file, index) => ({
    id: smartUploadJobId(file, index),
    index,
    file,
    fileName: file.name || `file-${index + 1}`,
    size: file.size || 0,
    status: "queued",
    progress: 4,
    step: "Menunggu antrean paralel",
    message: "File siap dianalisis.",
    result: null,
    error: "",
    startedAt: null,
    finishedAt: null,
  }));
}

function getSmartUploadParallelLimit(mode, totalFiles) {
  const limit = SMART_UPLOAD_PARALLEL_LIMITS[mode] || 2;
  return Math.max(1, Math.min(limit, totalFiles || 1));
}

async function runSmartUploadJobs(jobs, limit, worker) {
  let cursor = 0;
  const workerCount = Math.max(1, Math.min(limit, jobs.length));
  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (cursor < jobs.length) {
        const index = cursor;
        cursor += 1;
        await worker(jobs[index], index);
      }
    })
  );
}

async function waitForAnalysisJob(jobId, onProgress) {
  if (!jobId) throw new Error("Backend tidak mengembalikan ID job analisis.");
  const deadline = Date.now() + (30 * 60 * 1000);
  while (Date.now() < deadline) {
    const snapshot = await apiGet(`/api/analysis-runs/jobs/${encodeURIComponent(jobId)}`);
    onProgress?.(snapshot);
    const status = snapshot.job?.status;
    if (status === "completed") {
      if (!snapshot.result) throw new Error("Job selesai tanpa hasil analysis run.");
      return snapshot.result;
    }
    if (status === "failed") {
      throw new Error(snapshot.job?.error_message || "Job analisis gagal.");
    }
    if (status === "cancelled") {
      throw new Error("Job analisis dibatalkan.");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 450));
  }
  throw new Error("Job analisis melewati batas tunggu 30 menit.");
}

export default function SmartUploadPage({ onBack, onOpenGuidedReview }) {
  const [config, setConfig] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [files, setFiles] = useState([]);
  const [result, setResult] = useState(null);
  const [analysisJobs, setAnalysisJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [aiTesting, setAiTesting] = useState(false);
  const [aiDiagnostic, setAiDiagnostic] = useState(null);
  const [analysisMode, setAnalysisMode] = useState("fast");
  const [candidateLimit, setCandidateLimit] = useState(20);
  const [error, setError] = useState("");
  const [linkCrawlStatus, setLinkCrawlStatus] = useState(null);
  const [linkCrawlLoading, setLinkCrawlLoading] = useState(false);
  const [batchFile, setBatchFile] = useState(null);
  const [batchLimit, setBatchLimit] = useState(50);
  const [batchLocalOnly, setBatchLocalOnly] = useState(true);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batch, setBatch] = useState(null);
  const aggregateLinkCrawl = useMemo(() => aggregateLinkCrawlFromResult(result), [result]);

  useEffect(() => {
    apiGet("/api/smart-upload/config")
      .then(setConfig)
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!config?.analysis_pipeline_v2_enabled) return;
    setAnalysisMode("full");
    apiGet("/api/analysis-runs/readiness-dashboard")
      .then(setReadiness)
      .catch((err) => setError(err.message));
    apiGet("/api/analysis-runs/batch-intakes/recent?limit=1")
      .then((data) => {
        if (data?.batches?.[0]) setBatch(data.batches[0]);
      })
      .catch((err) => setError(err.message));
  }, [config?.analysis_pipeline_v2_enabled]);

  useEffect(() => {
    if (!batch?.id || batch.status !== "processing") return undefined;
    let cancelled = false;
    const pollBatch = async () => {
      try {
        const snapshot = await apiGet(`/api/analysis-runs/batch-intakes/${encodeURIComponent(batch.id)}`);
        if (!cancelled) setBatch(snapshot.batch);
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    };
    const interval = window.setInterval(pollBatch, 700);
    pollBatch();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [batch?.id, batch?.status]);

  useEffect(() => {
    if (!aggregateLinkCrawl?.total) return undefined;
    let cancelled = false;

    async function pollEvidenceLinks() {
      try {
        const status = await apiGet("/api/smart-upload/evidence-links/status");
        if (!cancelled) setLinkCrawlStatus(status);
      } catch {
        // Status polling is supporting UI only; main analysis errors remain visible elsewhere.
      }
    }

    pollEvidenceLinks();
    const interval = window.setInterval(pollEvidenceLinks, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [aggregateLinkCrawl?.total]);

  async function loadEvidenceLinkStatus({ silent = true } = {}) {
    try {
      const status = await apiGet("/api/smart-upload/evidence-links/status");
      setLinkCrawlStatus(status);
      return status;
    } catch (err) {
      if (!silent) setError(err.message);
      return null;
    }
  }

  async function startEvidenceLinkCrawl() {
    setLinkCrawlLoading(true);
    setError("");
    try {
      const status = await apiPost("/api/smart-upload/evidence-links/crawl");
      setLinkCrawlStatus(status);
      window.setTimeout(() => loadEvidenceLinkStatus(), 900);
    } catch (err) {
      setError(err.message);
    } finally {
      setLinkCrawlLoading(false);
    }
  }

  async function testAiConnection() {
    setAiTesting(true);
    setError("");
    setAiDiagnostic(null);
    try {
      const data = await apiGet("/api/smart-upload/ai-diagnostics");
      setAiDiagnostic(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setAiTesting(false);
    }
  }

  async function submitBatchZip(event) {
    event.preventDefault();
    if (!batchFile) {
      setError("Pilih satu file ZIP korpus terlebih dahulu.");
      return;
    }
    setBatchLoading(true);
    setBatch(null);
    setError("");
    try {
      const response = await apiUpload("/api/analysis-runs/batch-intakes", batchFile, {
        analysis_mode: "full_audit",
        review_limit: batchLimit,
        local_only: batchLocalOnly,
      });
      setBatch(response.batch);
    } catch (err) {
      setError(err.message);
    } finally {
      setBatchLoading(false);
    }
  }

  async function cancelBatch() {
    if (!batch?.id) return;
    try {
      const response = await apiPost(`/api/analysis-runs/batch-intakes/${encodeURIComponent(batch.id)}/cancel`);
      setBatch(response.batch);
    } catch (err) {
      setError(err.message);
    }
  }

  function updateAnalysisJob(jobId, patch) {
    setAnalysisJobs((current) =>
      current.map((job) => {
        if (job.id !== jobId) return job;
        const nextPatch = typeof patch === "function" ? patch(job) : patch;
        return { ...job, ...nextPatch };
      })
    );
  }

  function resetSelectedFiles(nextFiles) {
    setFiles(nextFiles);
    setAnalysisJobs([]);
    setResult(null);
    setLinkCrawlStatus(null);
    setError("");
  }

  async function analyzeFile(event) {
    event.preventDefault();
    await runAnalysisForFiles();
  }

  async function runAnalysisForFiles() {
    if (files.length === 0) {
      setError("Pilih minimal satu file evidence terlebih dahulu.");
      return;
    }
    const jobs = makeSmartUploadJobs(files);
    const usePipelineV2 = Boolean(config?.analysis_pipeline_v2_enabled);
    const parallelLimit = getSmartUploadParallelLimit(analysisMode, files.length);
    const results = new Array(files.length);

    setAnalysisJobs(jobs);
    setLoading(true);
    setError("");
    setResult(null);
    try {
      await runSmartUploadJobs(jobs, parallelLimit, async (job, index) => {
        const timers = [];
        const schedule = (delay, progress, step, message) => {
          timers.push(window.setTimeout(() => {
            updateAnalysisJob(job.id, (current) =>
              current.status === "running" ? { progress, step, message } : {}
            );
          }, delay));
        };

        updateAnalysisJob(job.id, {
          status: "running",
          progress: 12,
          step: "Mengirim file ke backend",
          message: "File dikirim sebagai request terpisah agar batch besar tidak membebani satu payload.",
          startedAt: Date.now(),
        });
        if (!usePipelineV2) {
          schedule(450, 32, "Ekstraksi dokumen", "Backend membaca teks, sheet, atau metadata yang tersedia.");
          schedule(1400, 58, "Analisis AI dan reasoning gate", "DeepSeek V4 dan gate deterministik menilai kandidat kertas kerja.");
          schedule(3200, 78, "Penyusunan rekomendasi", "Aplikasi menyiapkan kesimpulan evidence, kandidat upload, dan cek duplikat.");
        }

        try {
          let data;
          if (usePipelineV2) {
            const queued = await apiUpload("/api/analysis-runs", job.file, {
              analysis_mode: analysisMode === "fast" ? "screening" : "full_audit",
            });
            data = await waitForAnalysisJob(queued.job?.id, (snapshot) => {
              const lastEvent = snapshot.result?.events?.at(-1);
              updateAnalysisJob(job.id, {
                progress: lastEvent?.progress ?? (snapshot.job?.status === "queued" ? 8 : 12),
                step: lastEvent?.stage?.replaceAll("_", " ") || `Job ${snapshot.job?.status || "queued"}`,
                message: lastEvent?.message || "Job tersimpan dan menunggu worker multi-engine.",
              });
            });
          } else {
            data = await apiUpload("/api/smart-upload/recommendations", job.file, {
                analysis_mode: analysisMode,
                candidate_limit: candidateLimit,
            });
          }
          results[index] = data;
          const resultMessage = usePipelineV2
            ? `${data.mappings?.length ?? 0} kandidat parameter · coverage ${Math.round(data.run?.coverage_percentage ?? 0)}%.`
            : `${data.candidates?.length ?? 0} kandidat rekomendasi terbaca.`;
          updateAnalysisJob(job.id, {
            status: "done",
            progress: 100,
            step: "Selesai dianalisis",
            message: resultMessage,
            result: data,
            finishedAt: Date.now(),
          });
        } catch (err) {
          updateAnalysisJob(job.id, {
            status: "error",
            progress: 100,
            step: "Analisis gagal",
            message: err.message,
            error: err.message,
            finishedAt: Date.now(),
          });
        } finally {
          timers.forEach((timer) => window.clearTimeout(timer));
        }
      });

      const completed = results.filter(Boolean);
      if (completed.length === 1 && files.length === 1) {
        setResult(completed[0]);
      } else {
        setResult({
          count: completed.length,
          results: completed,
          progress_mode: "parallel",
          package_analysis: null,
          package_error: "",
        });
      }
      if (completed.length === 0) {
        setError("Semua file gagal dianalisis. Cek pesan error pada kartu progress masing-masing file.");
      }
      if (!usePipelineV2) loadEvidenceLinkStatus();
    } finally {
      setLoading(false);
    }
  }

  const featureEnabled = Boolean(config?.analysis_pipeline_v2_enabled || config?.enabled);

  return (
    <section className="smart-upload-page">
      <button className="back-button" type="button" onClick={onBack}>
        <ArrowLeft size={18} />
        Daftar Subunsur
      </button>

      <div className="smart-upload-header">
        <div>
          <p className="eyebrow">Smart Evidence</p>
          <h2>Upload Evidence Pintar</h2>
          <p>{config?.analysis_pipeline_v2_enabled
            ? "Unggah dokumen untuk memperoleh rekomendasi parameter, arah Grade, dan daftar bukti yang masih perlu dilengkapi."
            : "Analisis file memakai rekomendasi lokal dan diperkuat DeepSeek V4 bila API tersedia. Pilih mode berdasarkan kebutuhan akurasi, waktu tunggu, dan estimasi biaya."}</p>
        </div>
        <div className="smart-upload-mode">
          <StatusPill
            status={featureEnabled ? "Terisi" : "Kosong"}
            explanation={featureEnabled ? "Fitur analisis dokumen siap digunakan." : "Fitur belum diaktifkan di environment ini."}
          />
          <small>{config?.analysis_pipeline_v2_enabled ? "Analisis V2 · hasil diperiksa sebelum disimpan" : `${config?.ai_provider || "sumopod"} · ${config?.ai_model || "deepseek-v4-pro"} · ${config?.ai_configured ? "API tersambung" : "API belum tersambung"} · ${config?.require_ai ? "AI wajib" : "hybrid"}`}</small>
          {!config?.analysis_pipeline_v2_enabled ? (
            <button className="row-action-button" type="button" onClick={testAiConnection} disabled={aiTesting || !config?.ai_reasoning_enabled}>
              {aiTesting ? <Loader2 className="spin" size={15} /> : <Sparkles size={15} />}
              Tes AI
            </button>
          ) : null}
        </div>
      </div>

      <AnalysisModePicker value={analysisMode} onChange={setAnalysisMode} pipelineV2={config?.analysis_pipeline_v2_enabled} />
      {!config?.analysis_pipeline_v2_enabled ? <CandidateLimitControl value={candidateLimit} onChange={setCandidateLimit} /> : null}

      <form className="upload-analyzer" onSubmit={analyzeFile}>
        <label className="file-drop-zone">
          <UploadCloud size={28} />
          <span>{files.length > 0 ? `${files.length} file siap dianalisis` : "Pilih satu atau banyak file evidence"}</span>
          <small>{files.length > 0 ? `${formatBytes(files.reduce((total, item) => total + item.size, 0))} total` : "PDF, DOCX, XLSX, CSV, dan TXT dapat dianalisis sekaligus."}</small>
          <input type="file" multiple onChange={(event) => resetSelectedFiles(Array.from(event.target.files ?? []))} />
        </label>
        <button
          className="primary-button"
          type="submit"
          disabled={loading || !featureEnabled || files.length === 0}
          title={files.length === 0 ? "Pilih minimal satu file evidence terlebih dahulu." : "Jalankan analisis evidence."}
        >
          {loading ? <Loader2 className="spin" size={18} /> : <Sparkles size={18} />}
          Analisis Evidence
        </button>
      </form>

      {config?.analysis_pipeline_v2_enabled ? (
        <details className="smart-upload-advanced">
          <summary>Banyak dokumen dan informasi teknis</summary>
          <p className="smart-upload-advanced-intro">Gunakan bagian ini untuk ZIP berisi banyak dokumen atau untuk melihat kesiapan teknis sistem.</p>
          <BatchZipIntakePanel
            config={config?.batch_intake}
            file={batchFile}
            limit={batchLimit}
            localOnly={batchLocalOnly}
            loading={batchLoading}
            batch={batch}
            onFileChange={setBatchFile}
            onLimitChange={setBatchLimit}
            onLocalOnlyChange={setBatchLocalOnly}
            onSubmit={submitBatchZip}
            onCancel={cancelBatch}
            onOpenReview={onOpenGuidedReview}
          />
          <OperationalReadinessPanel data={readiness} />
        </details>
      ) : null}

      {files.length > 0 ? (
        <div className="selected-file-list">
          {files.map((item) => (
            <span key={`${item.name}-${item.size}-${item.lastModified}`}>{item.name} · {formatBytes(item.size)}</span>
          ))}
        </div>
      ) : null}

      {analysisJobs.length > 0 ? (
        <SmartUploadProgressBoard
          jobs={analysisJobs}
          loading={loading}
          parallelLimit={getSmartUploadParallelLimit(analysisMode, files.length)}
        />
      ) : null}

      {error ? <Notice tone="danger" text={error} /> : null}
      {aiDiagnostic ? <Notice tone={noticeToneForAi(aiDiagnostic.status)} text={`Tes AI: ${aiDiagnostic.message || aiDiagnostic.status}`} /> : null}
      {!config?.analysis_pipeline_v2_enabled && config && !config.ai_configured && config.require_ai ? <Notice tone="danger" text="DeepSeek API belum tersambung karena key belum terbaca di server. Mode ini mewajibkan DeepSeek V4, sehingga analisis tidak dapat dijalankan." /> : null}
      {!config?.analysis_pipeline_v2_enabled && config && !config.ai_configured && !config.require_ai ? <Notice tone="warning" text="DeepSeek API belum tersambung karena key belum terbaca di server. Mode hybrid tetap berjalan dengan rekomendasi lokal sampai key server tersedia." /> : null}
      {!config?.analysis_pipeline_v2_enabled && aggregateLinkCrawl ? (
        <LinkCrawlPanel
          linkCrawl={aggregateLinkCrawl}
          globalStatus={linkCrawlStatus}
          busy={loading || linkCrawlLoading}
          canReanalyze={files.length > 0 && !loading}
          onStartCrawl={startEvidenceLinkCrawl}
          onReanalyze={runAnalysisForFiles}
        />
      ) : null}
      {result ? <SmartUploadResults result={result} /> : null}
    </section>
  );
}
