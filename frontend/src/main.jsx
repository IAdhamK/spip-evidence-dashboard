import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertCircle,
  ArrowLeft,
  ArrowUpDown,
  CheckCircle2,
  Database,
  ExternalLink,
  FileArchive,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Info,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  UploadCloud,
} from "lucide-react";
import { apiGet, apiPost, apiUpload, apiUploadMany, isStaticSnapshot } from "./lib/api.js";
import "./styles/main.css";

const STATUS_ORDER = ["Kosong", "Terisi Sebagian", "Terisi", "Perlu Kurasi", "Final"];
const STATUS_ICONS = {
  Kosong: AlertCircle,
  "Terisi Sebagian": ArrowUpDown,
  Terisi: CheckCircle2,
  "Perlu Kurasi": TriangleAlert,
  Final: ShieldCheck,
};

function App() {
  const staticSnapshot = isStaticSnapshot();
  const [dashboard, setDashboard] = useState(null);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("Semua");
  const [kkFilter, setKkFilter] = useState("Semua");
  const [selected, setSelected] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSyncing, setDetailSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState(null);
  const [watchedFolder, setWatchedFolder] = useState(null);
  const [smartUploadOpen, setSmartUploadOpen] = useState(false);

  async function loadData({ silent = false } = {}) {
    if (!silent) setLoading(true);
    if (!silent) setError("");
    try {
      const [dashboardData, metaData] = await Promise.all([
        apiGet("/api/dashboard"),
        apiGet("/api/meta"),
      ]);
      setDashboard(dashboardData);
      setMeta(metaData);
    } catch (err) {
      if (!silent) setError(err.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function runSync() {
    if (staticSnapshot) {
      setError("Versi online GitHub Pages memakai snapshot read-only. Sinkronisasi live dijalankan dari aplikasi lokal/server backend.");
      return;
    }
    setSyncing(true);
    setError("");
    try {
      const status = await apiPost("/api/sync/background");
      setSyncStatus(status);
      await loadData({ silent: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setSyncing(false);
    }
  }

  async function openDetail(folder) {
    setDetailLoading(true);
    setSelected(null);
    try {
      const detail = await apiGet(`/api/subunsur/${encodeURIComponent(folder.kk_id)}/${encodeURIComponent(folder.kode)}`);
      setSelected(detail);
      startFolderBackgroundSync(folder.kk_id, folder.kode);
    } catch (err) {
      setError(err.message);
    } finally {
      setDetailLoading(false);
    }
  }

  async function refreshDetail(kkId, kode) {
    const detail = await apiGet(`/api/subunsur/${encodeURIComponent(kkId)}/${encodeURIComponent(kode)}`);
    setSelected(detail);
    return detail;
  }

  async function loadSyncStatus() {
    if (staticSnapshot) return null;
    try {
      const status = await apiGet("/api/sync/status");
      setSyncStatus(status);
      return status;
    } catch {
      return null;
    }
  }

  async function startFolderBackgroundSync(kkId, kode) {
    if (staticSnapshot) return null;
    try {
      const status = await apiPost(`/api/sync/background/${encodeURIComponent(kkId)}/${encodeURIComponent(kode)}`);
      setSyncStatus(status);
      return status;
    } catch (err) {
      setError(err.message);
      return null;
    }
  }

  async function syncSelectedDetail() {
    if (!selected || staticSnapshot || detailSyncing) return;
    setDetailSyncing(true);
    setError("");
    try {
      await startFolderBackgroundSync(selected.kk_id, selected.kode);
      await loadData({ silent: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setDetailSyncing(false);
    }
  }

  function watchFolder(folder) {
    if (staticSnapshot || !folder?.kk_id || !folder?.kode) return;
    setWatchedFolder({
      kkId: folder.kk_id,
      kode: folder.kode,
      expiresAt: Date.now() + 120000,
    });
    startFolderBackgroundSync(folder.kk_id, folder.kode);
  }

  useEffect(() => {
    loadData();
    loadSyncStatus();
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      loadData({ silent: true });
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (!selected || staticSnapshot) return undefined;
    const intervalId = window.setInterval(() => {
      syncSelectedDetail();
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [selected?.kk_id, selected?.kode, staticSnapshot, detailSyncing]);

  useEffect(() => {
    if (!selected) return undefined;
    const intervalId = window.setInterval(() => {
      refreshDetail(selected.kk_id, selected.kode);
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [selected?.kk_id, selected?.kode]);

  useEffect(() => {
    if (staticSnapshot) return undefined;
    const intervalId = window.setInterval(() => {
      loadSyncStatus();
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [staticSnapshot]);

  useEffect(() => {
    if (!watchedFolder || staticSnapshot) return undefined;
    const intervalId = window.setInterval(() => {
      if (Date.now() > watchedFolder.expiresAt) {
        setWatchedFolder(null);
        return;
      }
      startFolderBackgroundSync(watchedFolder.kkId, watchedFolder.kode);
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [watchedFolder?.kkId, watchedFolder?.kode, watchedFolder?.expiresAt, staticSnapshot]);

  const folders = dashboard?.folders ?? [];
  const filteredFolders = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return folders.filter((folder) => {
      const matchQuery =
        !needle ||
        folder.kode.toLowerCase().includes(needle) ||
        folder.subunsur_name.toLowerCase().includes(needle) ||
        folder.unsur.toLowerCase().includes(needle) ||
        folder.kk_title.toLowerCase().includes(needle);
      const matchStatus = statusFilter === "Semua" || folder.status === statusFilter;
      const matchKk = kkFilter === "Semua" || folder.kk_id === kkFilter;
      return matchQuery && matchStatus && matchKk;
    });
  }, [folders, query, statusFilter, kkFilter]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">SPIP Evidence Dashboard</p>
          <h1>Monitoring Evidence Lumbung File</h1>
        </div>
        <div className="topbar-actions">
          <button className="icon-button" onClick={loadData} aria-label="Refresh dashboard" title="Refresh dashboard">
            <RefreshCw size={18} />
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              setSelected(null);
              setSmartUploadOpen(true);
            }}
            title="Buka halaman rekomendasi folder evidence berbasis knowledge base"
          >
            <Sparkles size={18} />
            Upload Pintar
          </button>
          <button
            className="primary-button"
            onClick={runSync}
            disabled={syncing || syncStatus?.is_running || staticSnapshot}
            title={staticSnapshot ? "Versi online memakai snapshot read-only" : "Mulai sinkronisasi background dengan Lumbung File"}
          >
            {syncing || syncStatus?.is_running ? <Loader2 className="spin" size={18} /> : <Database size={18} />}
            {staticSnapshot ? "Snapshot" : syncStatus?.is_running ? "Sinkronisasi..." : "Sinkronkan"}
          </button>
        </div>
      </header>

      {staticSnapshot ? <Notice tone="info" text="Mode online: snapshot read-only dari data terakhir. Tombol Buka Folder tetap mengarah ke Lumbung File." /> : null}
      {syncStatus?.is_running ? <Notice tone="info" text={`Sinkronisasi ${syncStatus.scope} berjalan di background: ${syncStatus.synced}/${syncStatus.total} selesai.`} /> : null}
      {watchedFolder ? <Notice tone="info" text={`Mode pantau cepat aktif untuk ${watchedFolder.kkId}/${watchedFolder.kode}. Dashboard membaca data lokal tiap 1 detik.`} /> : null}
      {error ? <Notice tone="danger" text={error} /> : null}

      {detailLoading ? (
        <section className="detail-page loading-state">
          <Loader2 className="spin" size={28} />
          <span>Memuat halaman detail...</span>
        </section>
      ) : selected ? (
        <DetailPage
          detail={selected}
          meta={meta}
          onBack={() => setSelected(null)}
          onSync={syncSelectedDetail}
          onWatchFolder={watchFolder}
          syncing={detailSyncing}
          staticSnapshot={staticSnapshot}
        />
      ) : loading ? (
        <section className="loading-state">
          <Loader2 className="spin" size={28} />
          <span>Memuat dashboard evidence...</span>
        </section>
      ) : smartUploadOpen ? (
        <SmartUploadPage onBack={() => setSmartUploadOpen(false)} />
      ) : (
        <>
          <Summary dashboard={dashboard} meta={meta} />

          <section className="control-band">
            <label className="search-box">
              <Search size={18} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Cari kode, subunsur, unsur, atau KK"
              />
            </label>
            <SegmentedControl
              label="Filter status"
              options={["Semua", ...STATUS_ORDER]}
              value={statusFilter}
              onChange={setStatusFilter}
            />
          </section>

          <KkFilterBand
            value={kkFilter}
            onChange={setKkFilter}
            folders={folders}
          />

          <section className="content-grid">
            <FolderTable
              folders={filteredFolders}
              statusExplanations={meta?.status_explanations ?? {}}
              onOpenDetail={openDetail}
              onWatchFolder={watchFolder}
            />
          </section>
        </>
      )}
    </main>
  );
}

function Summary({ dashboard, meta }) {
  const counts = dashboard?.status_counts ?? {};
  return (
    <section className="summary-grid">
      <Metric label="Total Subunsur" value={dashboard?.total_folders ?? 0} hint="Total folder subunsur yang dipantau dari KK 3.1 sampai KK 3.4." />
      <Metric label="Total File" value={dashboard?.total_files ?? 0} hint="Jumlah file evidence yang sudah terbaca dari hasil sinkronisasi terakhir." />
      <Metric label="Terisi" value={counts.Terisi ?? 0} tone="success" hint={meta?.status_explanations?.Terisi} />
      <Metric label="Perlu Kurasi" value={counts["Perlu Kurasi"] ?? 0} tone="warning" hint={meta?.status_explanations?.["Perlu Kurasi"]} />
    </section>
  );
}

function Metric({ label, value, hint, tone = "neutral" }) {
  return (
    <article className={`metric metric-${tone}`}>
      <div className="metric-label">
        <span>{label}</span>
        <Tooltip text={hint} />
      </div>
      <strong>{value}</strong>
    </article>
  );
}

function KkFilterBand({ value, onChange, folders }) {
  const options = ["Semua", "KK3.1", "KK3.2", "KK3.3", "KK3.4"];
  const activeLabel = value === "Semua" ? "Semua KK" : value;
  const activeCount = value === "Semua"
    ? folders.length
    : folders.filter((folder) => folder.kk_id === value).length;

  return (
    <section className="kk-filter-band" aria-label="Filter KK">
      <div className="kk-filter-copy">
        <span>Filter KK</span>
        <strong>{activeLabel}</strong>
        <small>{activeCount} folder subunsur</small>
      </div>
      <SegmentedControl
        label="Filter KK"
        options={options}
        value={value}
        onChange={onChange}
        variant="kk"
      />
    </section>
  );
}

function FolderTable({ folders, statusExplanations, onOpenDetail, onWatchFolder }) {
  return (
    <section className="table-panel">
      <div className="section-heading">
        <div>
          <h2>Daftar Subunsur</h2>
          <p>{folders.length} folder sesuai filter saat ini</p>
        </div>
        <Legend statusExplanations={statusExplanations} />
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Kode</th>
              <th>Subunsur</th>
              <th>KK</th>
              <th>Status</th>
              <th className="numeric">File</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {folders.map((folder) => (
              <tr
                key={`${folder.kk_id}-${folder.kode}`}
              >
                <td className="code-cell">{folder.kode}</td>
                <td>
                  <div className="title-cell">
                    <span>{folder.subunsur_name}</span>
                    <small>{folder.unsur}</small>
                  </div>
                </td>
                <td>{folder.kk_id}</td>
                <td>
                  <StatusPill status={folder.status} explanation={statusExplanations[folder.status]} />
                </td>
                <td className="numeric">{folder.file_count}</td>
                <td>
                  <div className="row-actions">
                    <button
                      className="row-action-button"
                      type="button"
                      onClick={() => onOpenDetail(folder)}
                      title="Buka halaman detail parameter dan grade"
                    >
                      Detail
                    </button>
                    {folder.public_url ? (
                      <a
                        className="row-link-button"
                        href={folder.public_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={() => onWatchFolder(folder)}
                        title="Buka folder Lumbung File untuk upload evidence"
                      >
                        <ExternalLink size={15} />
                        Lumbung
                      </a>
                    ) : (
                      <span className="row-link-disabled" title="Isi LUMBUNG_SHARE_TOKEN untuk membuat link folder">
                        Lumbung
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {folders.length === 0 ? <EmptyState text="Tidak ada folder yang cocok dengan filter saat ini." /> : null}
    </section>
  );
}

function DetailPage({ detail, meta, onBack, onSync, onWatchFolder, syncing, staticSnapshot }) {
  const files = detail.files ?? [];
  return (
    <section className="detail-page">
      <button className="back-button" type="button" onClick={onBack}>
        <ArrowLeft size={18} />
        Daftar Subunsur
      </button>

      <div className="detail-page-header">
        <div>
          <p className="eyebrow">{detail.kk_id} / {detail.kode}</p>
          <h2>{detail.subunsur_name}</h2>
          <p>{detail.unsur}</p>
        </div>
        <div className="detail-header-actions">
          <StatusPill status={detail.status} explanation={meta?.status_explanations?.[detail.status]} />
          <button
            className="secondary-button"
            type="button"
            onClick={onSync}
            disabled={syncing || staticSnapshot}
            title={staticSnapshot ? "Versi snapshot tidak bisa sinkronisasi live" : "Sinkronkan hanya subunsur ini"}
          >
            {syncing ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
            Sinkronkan Subunsur Ini
          </button>
          <span className="autosync-note">Auto-sync subunsur aktif tiap 5 detik.</span>
        </div>
      </div>

      <div className="detail-status">
        <StatusPill status={detail.status} explanation={meta?.status_explanations?.[detail.status]} />
        <p>{detail.status_reason}</p>
      </div>

      {detail.matrix_subunsur_name && detail.matrix_subunsur_name !== detail.subunsur_name ? (
        <InfoBlock label="Nama Subunsur di Matriks" text={detail.matrix_subunsur_name} />
      ) : null}

      <ParameterList parameters={detail.parameters ?? []} kkId={detail.kk_id} kode={detail.kode} />

      <InfoBlock label="Panduan Evidence Umum" text={detail.evidence_hint} />

      <div className="detail-actions">
        {detail.public_url ? (
          <a
            className="primary-button link-button"
            href={detail.public_url}
            target="_blank"
            rel="noreferrer"
            onClick={() => onWatchFolder(detail)}
          >
            <ExternalLink size={18} />
            Buka Folder
          </a>
        ) : (
          <span className="disabled-link">Link tersedia setelah sinkronisasi</span>
        )}
      </div>

      <section className="file-list">
        <h3>Daftar File ({detail.file_count})</h3>
        {files.length === 0 ? (
          <EmptyState text="Belum ada evidence yang terbaca. Klik Buka Folder untuk mengunggah melalui Lumbung File." />
        ) : (
          files.map((file) => <FileItem file={file} key={file.id} />)
        )}
      </section>
    </section>
  );
}


function SmartUploadPage({ onBack }) {
  const [config, setConfig] = useState(null);
  const [files, setFiles] = useState([]);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [aiTesting, setAiTesting] = useState(false);
  const [aiDiagnostic, setAiDiagnostic] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiGet("/api/smart-upload/config")
      .then(setConfig)
      .catch((err) => setError(err.message));
  }, []);

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

  async function analyzeFile(event) {
    event.preventDefault();
    if (files.length === 0) {
      setError("Pilih minimal satu file evidence terlebih dahulu.");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = files.length === 1
        ? await apiUpload("/api/smart-upload/recommendations", files[0])
        : await apiUploadMany("/api/smart-upload/recommendations/batch", files);
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="smart-upload-page">
      <button className="back-button" type="button" onClick={onBack}>
        <ArrowLeft size={18} />
        Daftar Subunsur
      </button>

      <div className="smart-upload-header">
        <div>
          <p className="eyebrow">DEV Preparation</p>
          <h2>Upload Evidence Pintar</h2>
          <p>Analisis file untuk menemukan kandidat KK, detail parameter, dan Grade tujuan sebelum upload ke Lumbung File.</p>
        </div>
        <div className="smart-upload-mode">
          <StatusPill
            status={config?.enabled ? "Terisi Sebagian" : "Kosong"}
            explanation={config?.enabled ? "Fitur aktif di DEV, tetapi upload otomatis tetap menunggu konfirmasi." : "Fitur belum diaktifkan di environment ini."}
          />
          <small>{config?.ai_provider || "deepseek"} · {config?.ai_model || "deepseek-v4-flash"}</small>
          <button className="row-action-button" type="button" onClick={testAiConnection} disabled={aiTesting || !config?.ai_reasoning_enabled}>
            {aiTesting ? <Loader2 className="spin" size={15} /> : <Sparkles size={15} />}
            Tes AI
          </button>
        </div>
      </div>

      <form className="upload-analyzer" onSubmit={analyzeFile}>
        <label className="file-drop-zone">
          <UploadCloud size={28} />
          <span>{files.length > 0 ? `${files.length} file siap dianalisis` : "Pilih satu atau banyak file evidence"}</span>
          <small>{files.length > 0 ? `${formatBytes(files.reduce((total, item) => total + item.size, 0))} total` : "PDF, DOCX, XLSX, CSV, dan TXT dapat dianalisis sekaligus."}</small>
          <input type="file" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
        </label>
        <button className="primary-button" type="submit" disabled={loading || !config?.enabled}>
          {loading ? <Loader2 className="spin" size={18} /> : <Sparkles size={18} />}
          Analisis Evidence
        </button>
      </form>

      {files.length > 0 ? (
        <div className="selected-file-list">
          {files.map((item) => (
            <span key={`${item.name}-${item.size}-${item.lastModified}`}>{item.name} · {formatBytes(item.size)}</span>
          ))}
        </div>
      ) : null}

      {error ? <Notice tone="danger" text={error} /> : null}
      {aiDiagnostic ? <Notice tone={noticeToneForAi(aiDiagnostic.status)} text={`Tes AI: ${aiDiagnostic.message || aiDiagnostic.status}`} /> : null}
      {config && !config.ai_configured ? <Notice tone="info" text="AI key belum terbaca. Sistem tetap memakai pencocokan knowledge base lokal." /> : null}
      {result ? <SmartUploadResults result={result} /> : null}
    </section>
  );
}

function SmartUploadResults({ result }) {
  if (Array.isArray(result?.results)) {
    return (
      <section className="batch-result-panel">
        <div className="section-heading compact-heading">
          <div>
            <h3>Hasil Analisis Batch</h3>
            <p>{result.count} file selesai dianalisis.</p>
          </div>
        </div>
        <div className="batch-result-list">
          {result.results.map((item, index) => (
            <SmartUploadResult key={item.review_id || index} result={item} ordinal={index + 1} />
          ))}
        </div>
      </section>
    );
  }
  return <SmartUploadResult result={result} />;
}

function SmartUploadResult({ result, ordinal }) {
  const candidates = result.candidates ?? [];
  const [uploadingIndex, setUploadingIndex] = useState(null);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState("");
  const uploadAllowed = Boolean(result.upload?.allow_real_upload);

  async function confirmUpload(index) {
    setUploadingIndex(index);
    setUploadError("");
    setUploadResult(null);
    try {
      const data = await apiPost("/api/smart-upload/confirm-upload", {
        review_id: result.review_id,
        candidate_index: index,
      });
      setUploadResult(data);
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setUploadingIndex(null);
    }
  }

  return (
    <section className="smart-result-panel">
      <div className="section-heading compact-heading">
        <div>
          <h3>{ordinal ? `Hasil Rekomendasi #${ordinal}` : "Hasil Rekomendasi"}</h3>
          <p>{result.file?.name} · {formatBytes(result.file?.size_bytes)}</p>
        </div>
        <div className="ai-status-box">
          <strong>AI</strong>
          <span>{result.ai?.status || "skipped"}</span>
        </div>
      </div>
      {result.extraction ? <Notice tone={result.extraction.status === "ok" ? "info" : "neutral"} text={`Ekstraksi ${result.extraction.method}: ${result.extraction.message || result.extraction.status}`} /> : null}
      {result.ai?.message ? <Notice tone={noticeToneForAi(result.ai.status)} text={result.ai.message} /> : null}
      {!uploadAllowed ? <Notice tone="info" text="Upload sungguhan masih dikunci di DEV. Ubah SMART_UPLOAD_ALLOW_REAL_UPLOAD=true hanya saat siap mengirim file ke Lumbung File." /> : null}
      {uploadError ? <Notice tone="danger" text={uploadError} /> : null}
      {uploadResult ? <Notice tone="info" text={`Upload berhasil: ${uploadResult.message}`} /> : null}
      {result.preview_text ? (
        <div className="preview-box">
          <span>Preview Teks</span>
          <p>{result.preview_text}</p>
        </div>
      ) : null}
      <div className="candidate-list">
        {candidates.map((candidate, index) => (
          <article className="candidate-card" key={`${candidate.kk_id}-${candidate.detail_kode}-${candidate.grade}-${index}`}>
            <div className="candidate-rank">#{index + 1}</div>
            <div className="candidate-body">
              <div className="candidate-title-row">
                <div>
                  <strong>{candidate.kk_id} / {candidate.kode} / {candidate.detail_kode} · Grade {candidate.grade}</strong>
                  <p>{candidate.subunsur_name}</p>
                </div>
                <span className="confidence-pill">{Math.round((candidate.confidence ?? 0) * 100)}%</span>
              </div>
              <p className="candidate-parameter">{candidate.uraian}</p>
              <div className="candidate-reason">
                {(candidate.reasons ?? []).map((reason) => <span key={reason}>{reason}</span>)}
              </div>
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
                    onClick={() => confirmUpload(index)}
                    disabled={!uploadAllowed || uploadingIndex !== null}
                    title={uploadAllowed ? "Konfirmasi upload file ke folder kandidat ini" : "Upload sungguhan masih dikunci di DEV"}
                  >
                    {uploadingIndex === index ? <Loader2 className="spin" size={15} /> : <CheckCircle2 size={15} />}
                    Konfirmasi Upload
                  </button>
                </div>
              </div>
            </div>
          </article>
        ))}
      </div>
      {candidates.length === 0 ? <EmptyState text="Belum ada kandidat yang cukup kuat. File ini perlu kurasi manual." /> : null}
    </section>
  );
}

function ParameterList({ parameters, kkId, kode }) {
  return (
    <section className="parameter-list">
      <h3>
        Acuan Parameter Matriks
        <Tooltip text={`Parameter ini diambil per kombinasi ${kkId} + ${kode}, sehingga tidak disamakan otomatis dengan KK lain.`} />
      </h3>
      {parameters.length === 0 ? (
        <EmptyState text="Parameter matriks belum tersedia untuk kombinasi KK dan subunsur ini." />
      ) : (
        parameters.map((parameter) => (
          <article className="parameter-item" key={parameter.id}>
            <div className="parameter-meta">
              <span>Detail {parameter.detail_kode || `${kode}.${parameter.parameter_no || "-"}`}</span>
              <span>No {parameter.parameter_no || "-"}</span>
              <span>Baris {parameter.source_row}</span>
              <span>{compactCodes(parameter)}</span>
            </div>
            <p className="parameter-statement">{parameter.uraian}</p>
            <GradeMatrix parameter={parameter} />
            {parameter.cara_pengujian ? (
              <div className="test-method">
                <strong>
                  Cara Pengujian
                  <Tooltip text="Kode ini berasal dari matriks. W = Wawancara, D = Dokumen, O = Observasi." />
                </strong>
                <span className="method-code">{parameter.cara_pengujian}</span>
                <div className="method-list">
                  {expandTestMethods(parameter.cara_pengujian).map((method) => (
                    <span key={method.code}>
                      <b>{method.code}</b>
                      {method.label}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </article>
        ))
      )}
    </section>
  );
}

function GradeMatrix({ parameter }) {
  const grades = parameter.grades?.length
    ? parameter.grades
    : [
        {
          grade: parameter.grade_sample,
          kriteria: parameter.kriteria_sample,
          penjelasan: parameter.penjelasan_sample,
        },
      ];

  return (
    <div className="grade-matrix" aria-label="Rincian grade kertas kerja">
      {grades.map((grade) => (
        <article className="grade-row" key={`${parameter.id}-${grade.grade}`}>
          <div className="grade-badge">
            <span>Grad.</span>
            <strong>{grade.grade || "-"}</strong>
          </div>
          <div className="grade-copy">
            <MatrixField label="Kriteria" text={grade.kriteria} />
            <MatrixField label="Penjelasan" text={grade.penjelasan} />
            <GradeEvidenceFolder folder={grade.evidence_folders?.[0]} />
          </div>
        </article>
      ))}
    </div>
  );
}

function GradeEvidenceFolder({ folder }) {
  if (!folder) {
    return (
      <div className="grade-folder-slot empty-evidence-folders">
        Folder evidence grade belum tersedia.
      </div>
    );
  }

  const status = evidenceFolderStatus(folder);
  const Icon = status.icon;
  return (
    <article className={`grade-folder-slot evidence-folder-${status.tone}`}>
      <div>
        <span className="evidence-folder-title">
          <Icon size={15} />
          Folder Evidence Grade {folder.grade}
        </span>
        <strong>{folder.file_count ?? 0} file</strong>
      </div>
      {folder.public_url ? (
        <a href={folder.public_url} target="_blank" rel="noreferrer" title="Buka folder grade di Lumbung File">
          <ExternalLink size={15} />
          Buka
        </a>
      ) : (
        <small>Link belum aktif</small>
      )}
    </article>
  );
}

function evidenceFolderStatus(folder) {
  if (folder.error_message) {
    return { tone: "warning", icon: TriangleAlert };
  }
  if ((folder.file_count ?? 0) > 0) {
    return { tone: "success", icon: CheckCircle2 };
  }
  return { tone: "empty", icon: AlertCircle };
}

function MatrixField({ label, text }) {
  return (
    <div className="matrix-field">
      <span>{label}</span>
      <p>{text || "Belum tersedia pada data matriks."}</p>
    </div>
  );
}

function InfoBlock({ label, text }) {
  return (
    <section className="info-block">
      <span>{label}</span>
      <p>{text}</p>
    </section>
  );
}

function compactCodes(parameter) {
  const parts = [
    parameter.kode_spip && `SPIP: ${parameter.kode_spip}`,
    parameter.kode_mri && `MRI: ${parameter.kode_mri}`,
    parameter.kode_iepk && `IEPK: ${parameter.kode_iepk}`,
  ].filter(Boolean);
  return parts.join(" · ");
}

function expandTestMethods(value) {
  const labels = {
    W: "Wawancara",
    D: "Analisis Dokumen",
    O: "Observasi",
  };
  return String(value || "")
    .split("/")
    .map((code) => code.trim().toUpperCase())
    .filter(Boolean)
    .map((code) => ({ code, label: labels[code] || "Metode lain" }));
}

function FileItem({ file }) {
  const Icon = fileIcon(file);
  return (
    <article className="file-item">
      <Icon size={18} />
      <div>
        <strong>{file.name}</strong>
        <span>{formatBytes(file.size_bytes)} · {file.mime_type || "folder"} · {formatDate(file.modified_at)}</span>
      </div>
    </article>
  );
}

function fileIcon(file) {
  if (file.is_folder) return FolderOpen;
  if (file.mime_type?.includes("spreadsheet")) return FileSpreadsheet;
  if (file.mime_type?.includes("zip")) return FileArchive;
  return FileText;
}

function StatusPill({ status, explanation }) {
  const Icon = STATUS_ICONS[status] ?? Info;
  return (
    <span className={`status-pill status-${slug(status)}`}>
      <Icon size={15} />
      {status}
      <Tooltip text={explanation} />
    </span>
  );
}

function Tooltip({ text }) {
  if (!text) return null;
  return (
    <span className="tooltip" tabIndex="0" aria-label={text}>
      <Info size={14} />
      <span className="tooltip-bubble">{text}</span>
    </span>
  );
}

function Legend({ statusExplanations }) {
  return (
    <div className="legend">
      {STATUS_ORDER.map((status) => (
        <StatusPill key={status} status={status} explanation={statusExplanations[status]} />
      ))}
    </div>
  );
}

function SegmentedControl({ label, options, value, onChange, variant = "default" }) {
  return (
    <div className={`segmented segmented-${variant}`} aria-label={label}>
      {options.map((option) => (
        <button
          key={option}
          className={option === value ? "active" : ""}
          onClick={() => onChange(option)}
          type="button"
        >
          {option}
        </button>
      ))}
    </div>
  );
}

function noticeToneForAi(status) {
  if (status === "ok") return "info";
  if (status === "unavailable" || status === "skipped") return "neutral";
  return "danger";
}

function Notice({ text, tone = "neutral" }) {
  return <div className={`notice notice-${tone}`}>{text}</div>;
}

function EmptyState({ text }) {
  return <div className="empty-state">{text}</div>;
}

function slug(value) {
  return value.toLowerCase().replaceAll(" ", "-");
}

function formatBytes(value) {
  if (!value) return "0 KB";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}

function formatDate(value) {
  if (!value) return "Belum ada tanggal";
  try {
    return new Intl.DateTimeFormat("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

createRoot(document.getElementById("root")).render(<App />);
