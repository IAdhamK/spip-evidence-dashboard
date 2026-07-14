import { FileArchive, ListChecks, Loader2 } from "lucide-react";
import { formatBytes } from "../../lib/formatters.js";
import { Notice } from "../shared/Feedback.jsx";


export default function BatchZipIntakePanel({
  config,
  file,
  limit,
  localOnly,
  loading,
  batch,
  onFileChange,
  onLimitChange,
  onLocalOnlyChange,
  onSubmit,
  onCancel,
  onOpenReview,
}) {
  const progress = batch?.progress || {};
  const percentage = Math.max(0, Math.min(100, Math.round(progress.percentage || 0)));
  const terminal = ["completed", "completed_with_errors", "rejected"].includes(batch?.status);
  const visibleMembers = (batch?.members || []).filter((item) => item.job_id || item.member_status === "rejected").slice(0, 12);
  return (
    <section className="batch-intake-panel" aria-label="Intake ZIP korpus">
      <div className="batch-intake-heading">
        <div>
          <p className="eyebrow">Cara termudah untuk banyak dokumen</p>
          <h3>Masukkan ZIP menjadi antrean review</h3>
          <p>Aplikasi memeriksa keamanan ZIP terlebih dahulu, memilih dokumen beragam, lalu menjalankan Full Audit V2 per file.</p>
        </div>
        <span>ZIP → V2 → Review Terpandu</span>
      </div>
      <form className="batch-intake-form" onSubmit={onSubmit}>
        <label className="batch-file-picker">
          <FileArchive size={24} />
          <span>{file ? file.name : "Pilih ZIP korpus"}</span>
          <small>{file ? formatBytes(file.size) : `Maksimal ${formatBytes(config?.max_archive_bytes || 0)}`}</small>
          <input type="file" accept=".zip,application/zip" onChange={(event) => onFileChange(event.target.files?.[0] || null)} />
        </label>
        <label className="batch-limit-field">
          <span>Jumlah dokumen untuk review</span>
          <input
            type="number"
            min="1"
            max={config?.max_files || 200}
            value={limit}
            onChange={(event) => onLimitChange(Math.max(1, Math.min(config?.max_files || 200, Number(event.target.value) || 1)))}
          />
          <small>50 disarankan untuk tahap awal.</small>
        </label>
        <label className="batch-local-toggle">
          <input type="checkbox" checked={localOnly} onChange={(event) => onLocalOnlyChange(event.target.checked)} />
          <span><strong>Proses lokal saja</strong><small>Isi dokumen tidak dikirim ke DeepSeek/Sumopod.</small></span>
        </label>
        <button className="primary-button" type="submit" disabled={loading || !file}>
          {loading ? <Loader2 className="spin" size={18} /> : <ListChecks size={18} />}
          Periksa & buat antrean
        </button>
      </form>
      {!localOnly ? <Notice tone="warning" text="Mode lokal dimatikan. Engine model yang aktif dapat mengirim cuplikan dokumen ke DeepSeek V4 Pro melalui Sumopod." /> : null}
      {batch ? (
        <div className="batch-intake-progress">
          <div className="batch-progress-title">
            <div>
              <strong>{batch.archive_file_name}</strong>
              <span>{batch.status?.replaceAll("_", " ")} · batch {batch.id.slice(0, 8)}</span>
            </div>
            <strong>{percentage}%</strong>
          </div>
          <div className="guided-progress-track"><span style={{ width: `${percentage}%` }} /></div>
          <div className="batch-summary-grid">
            <div><span>Masuk antrean</span><strong>{batch.enqueued_count || 0}</strong></div>
            <div><span>Selesai</span><strong>{progress.completed || 0}</strong></div>
            <div><span>Duplikat</span><strong>{batch.duplicate_count || 0}</strong></div>
            <div><span>Ditolak</span><strong>{batch.rejected_count || 0}</strong></div>
            <div><span>Dilewati</span><strong>{batch.skipped_count || 0}</strong></div>
          </div>
          {visibleMembers.length > 0 ? (
            <div className="batch-member-list">
              {visibleMembers.map((item) => (
                <div key={item.id || item.archive_path}>
                  <span>{item.file_name}</span>
                  <strong>{item.job_status || item.member_status}</strong>
                  {item.reason || item.job_error_message ? <small>{item.reason || item.job_error_message}</small> : null}
                </div>
              ))}
            </div>
          ) : null}
          <div className="batch-actions">
            {batch.status === "processing" ? <button className="secondary-button" type="button" onClick={onCancel}>Batalkan batch</button> : null}
            {terminal && progress.completed > 0 ? (
              <button className="primary-button" type="button" onClick={onOpenReview}>
                <ListChecks size={17} />
                Mulai Review Terpandu
              </button>
            ) : null}
          </div>
          {batch.error_message ? <Notice tone="danger" text={batch.error_message} /> : null}
        </div>
      ) : null}
    </section>
  );
}
