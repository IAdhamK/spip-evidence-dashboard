import { AlertCircle, CheckCircle2, Clock3, Loader2 } from "lucide-react";
import { formatBytes } from "../../lib/formatters.js";
import { clampCandidateLimit } from "./utils.js";


const ANALYSIS_MODE_OPTIONS = [
  {
    value: "fast",
    label: "Mode Cepat",
    title: "Screening awal",
    description: "Nama file dan cuplikan awal. Paling murah dan cepat.",
  },
  {
    value: "deep",
    label: "Mode Mendalam",
    title: "Review terarah",
    description: "Cuplikan awal, tengah, akhir, dan halaman kunci. Akurasi lebih baik.",
  },
  {
    value: "full",
    label: "Mode Penuh",
    title: "Review terpanjang",
    description: "Ekstraksi paling panjang dalam satu request AI. Lebih lama dan lebih mahal.",
  },
];

const ANALYSIS_V2_MODE_OPTIONS = [
  {
    value: "fast",
    label: "Pemeriksaan Awal",
    title: "Hasil lebih cepat",
    description: "Membaca bagian penting dokumen untuk melihat kemungkinan parameter. Arah Grade belum lengkap.",
  },
  {
    value: "full",
    label: "Pemeriksaan Lengkap",
    title: "Disarankan untuk penilaian",
    description: "Membaca seluruh bagian dokumen untuk menentukan parameter, arah Grade, dan bukti yang masih kurang.",
  },
];

const SMART_UPLOAD_JOB_LABELS = {
  queued: "Antre",
  running: "Berjalan",
  done: "Selesai",
  error: "Gagal",
};

export function SmartUploadProgressBoard({ jobs, loading, parallelLimit }) {
  const doneCount = jobs.filter((job) => job.status === "done").length;
  const errorCount = jobs.filter((job) => job.status === "error").length;
  const runningCount = jobs.filter((job) => job.status === "running").length;
  return (
    <section className="smart-progress-board" aria-label="Progress analisis file">
      <div className="smart-progress-heading">
        <div>
          <h3>Progress Analisis File</h3>
          <p>
            {loading
              ? `${runningCount} file sedang diproses paralel, maksimal ${parallelLimit} file bersamaan.`
              : `${doneCount} selesai, ${errorCount} gagal.`}
          </p>
        </div>
        <span>{doneCount + errorCount}/{jobs.length} selesai</span>
      </div>
      <div className="smart-progress-grid">
        {jobs.map((job) => <SmartUploadProgressCard key={job.id} job={job} />)}
      </div>
    </section>
  );
}

function SmartUploadProgressCard({ job }) {
  const progress = Math.max(0, Math.min(100, Math.round(job.progress || 0)));
  const label = SMART_UPLOAD_JOB_LABELS[job.status] || job.status;
  const elapsed = job.startedAt && job.finishedAt
    ? `${Math.max(1, Math.round((job.finishedAt - job.startedAt) / 1000))} dtk`
    : null;
  const Icon = job.status === "done"
    ? CheckCircle2
    : job.status === "error"
      ? AlertCircle
      : job.status === "running"
        ? Loader2
        : Clock3;
  return (
    <article className={`smart-progress-card ${job.status}`}>
      <div className="smart-progress-top">
        <div className="smart-progress-file">
          <span>File {job.index + 1}</span>
          <strong>{job.fileName}</strong>
          <small>{formatBytes(job.size)}</small>
        </div>
        <span className="smart-progress-state">
          <Icon className={job.status === "running" ? "spin" : undefined} size={15} />
          {label}
        </span>
      </div>
      <div className="smart-progress-bar" aria-label={`Progress ${progress}%`}>
        <span style={{ width: `${progress}%` }} />
      </div>
      <div className="smart-progress-meta">
        <span>{job.step}</span>
        <strong>{progress}%</strong>
      </div>
      {job.message ? (
        <p className={job.status === "error" ? "smart-progress-error" : "smart-progress-message"}>{job.message}</p>
      ) : null}
      {elapsed ? <small className="smart-progress-elapsed">Durasi {elapsed}</small> : null}
    </article>
  );
}

export function AnalysisModePicker({ value, onChange, pipelineV2 = false }) {
  const options = pipelineV2 ? ANALYSIS_V2_MODE_OPTIONS : ANALYSIS_MODE_OPTIONS;
  const normalizedValue = options.some((item) => item.value === value) ? value : options[0].value;
  return (
    <section className="analysis-mode-panel" aria-label="Jenis pemeriksaan dokumen">
      <div className="analysis-mode-heading">
        <div>
          <h3>{pipelineV2 ? "Jenis Pemeriksaan" : "Mode Analisis"}</h3>
          <p>{pipelineV2 ? "Pilih pemeriksaan awal atau pemeriksaan lengkap." : "Pilih seberapa banyak konteks dokumen yang dikirim ke DeepSeek V4."}</p>
        </div>
        <span>{options.find((item) => item.value === normalizedValue)?.label}</span>
      </div>
      <div className="analysis-mode-grid">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === normalizedValue ? "analysis-mode-card active" : "analysis-mode-card"}
            onClick={() => onChange(option.value)}
          >
            <strong>{option.label}</strong>
            <span>{option.title}</span>
            <small>{option.description}</small>
          </button>
        ))}
      </div>
    </section>
  );
}


export function CandidateLimitControl({ value, onChange }) {
  const presets = [5, 10, 20, 50];
  return (
    <section className="candidate-limit-panel" aria-label="Batas kandidat kertas kerja">
      <div>
        <h3>Batas Kandidat Kertas Kerja</h3>
        <p>Semua KK tetap eligible. Angka ini hanya membatasi berapa kandidat teratas yang dikirim ke DeepSeek untuk satu putaran uji.</p>
      </div>
      <div className="candidate-limit-controls">
        <label>
          <span>Limit kandidat</span>
          <input
            type="number"
            min="1"
            max="100"
            value={value}
            onChange={(event) => onChange(clampCandidateLimit(event.target.value))}
          />
        </label>
        <div className="candidate-limit-presets" aria-label="Preset limit kandidat">
          {presets.map((preset) => (
            <button
              key={preset}
              type="button"
              className={Number(value) === preset ? "active" : ""}
              onClick={() => onChange(preset)}
            >
              {preset}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
