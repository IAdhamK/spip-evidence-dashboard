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
const STATUS_ICONS = {
  Kosong: AlertCircle,
  "Terisi Sebagian": ArrowUpDown,
  Terisi: CheckCircle2,
  "Perlu Kurasi": TriangleAlert,
  Final: ShieldCheck,
};

function parseRouteHash() {
  if (typeof window === "undefined") return { page: "dashboard" };
  const hash = window.location.hash || "";
  if (hash === "#/smart-upload") return { page: "smart-upload" };
  if (hash.startsWith("#/detail/")) {
    const [, , kkId, kode] = hash.split("/");
    if (kkId && kode) {
      return {
        page: "detail",
        kkId: decodeURIComponent(kkId),
        kode: decodeURIComponent(kode),
      };
    }
  }
  return { page: "dashboard" };
}

function updateRouteHash(route) {
  if (typeof window === "undefined") return;
  let nextHash = "";
  if (route?.page === "smart-upload") {
    nextHash = "#/smart-upload";
  } else if (route?.page === "detail" && route.kkId && route.kode) {
    nextHash = `#/detail/${encodeURIComponent(route.kkId)}/${encodeURIComponent(route.kode)}`;
  }
  if (!nextHash) {
    window.history.pushState(null, "", `${window.location.pathname}${window.location.search}`);
    return;
  }
  if (window.location.hash !== nextHash) {
    window.history.pushState(null, "", `${window.location.pathname}${window.location.search}${nextHash}`);
  }
}

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
  const [smartUploadConfig, setSmartUploadConfig] = useState(null);

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

  async function openDetail(folder, { updateRoute = true } = {}) {
    setDetailLoading(true);
    setSelected(null);
    setSmartUploadOpen(false);
    try {
      const detail = await apiGet(`/api/subunsur/${encodeURIComponent(folder.kk_id)}/${encodeURIComponent(folder.kode)}`);
      setSelected(detail);
      if (updateRoute) {
        updateRouteHash({ page: "detail", kkId: folder.kk_id, kode: folder.kode });
      }
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

  async function restoreRouteFromHash() {
    const route = parseRouteHash();
    if (route.page === "smart-upload") {
      setSelected(null);
      setDetailLoading(false);
      setSmartUploadOpen(true);
      return;
    }
    if (route.page === "detail") {
      setSmartUploadOpen(false);
      await openDetail({ kk_id: route.kkId, kode: route.kode }, { updateRoute: false });
      return;
    }
    setSelected(null);
    setDetailLoading(false);
    setSmartUploadOpen(false);
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

  async function loadSmartUploadConfig() {
    try {
      const config = await apiGet("/api/smart-upload/config");
      setSmartUploadConfig(config);
      if (!config?.enabled) setSmartUploadOpen(false);
      return config;
    } catch {
      setSmartUploadConfig(null);
      setSmartUploadOpen(false);
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
    loadSmartUploadConfig();
    restoreRouteFromHash();
    const handleRouteChange = () => {
      restoreRouteFromHash();
    };
    window.addEventListener("hashchange", handleRouteChange);
    window.addEventListener("popstate", handleRouteChange);
    return () => {
      window.removeEventListener("hashchange", handleRouteChange);
      window.removeEventListener("popstate", handleRouteChange);
    };
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
          {smartUploadConfig?.enabled ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setSelected(null);
                setSmartUploadOpen(true);
                updateRouteHash({ page: "smart-upload" });
              }}
              title="Buka halaman rekomendasi folder evidence berbasis knowledge base"
            >
              <Sparkles size={18} />
              Upload Pintar
            </button>
          ) : null}
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
          onBack={() => {
            setSelected(null);
            updateRouteHash({ page: "dashboard" });
          }}
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
        <SmartUploadPage
          onBack={() => {
            setSmartUploadOpen(false);
            updateRouteHash({ page: "dashboard" });
          }}
        />
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
            {folders.map((folder) => {
              const lumbungUrl = folder.parameter_entry_public_url || folder.public_url;
              const lumbungTitle = folder.parameter_entry_public_url
                ? `Buka struktur parameter ${folder.parameter_entry_detail_kode || ""} di Lumbung File`
                : "Buka folder Lumbung File untuk upload evidence";
              return (
              <tr
                key={`${folder.kk_id}-${folder.kode}`}
              >
                <td className="code-cell">{folder.kode}</td>
                <td>
                  <button
                    className="title-cell subunsur-detail-trigger"
                    type="button"
                    onClick={() => onOpenDetail(folder)}
                    title="Buka detail subunsur"
                  >
                    <span>{folder.subunsur_name}</span>
                    <small>{folder.unsur}</small>
                  </button>
                </td>
                <td>{folder.kk_id}</td>
                <td>
                  <StatusPill status={folder.status} explanation={statusExplanations[folder.status]} />
                </td>
                <td className="numeric">{folder.file_count}</td>
                <td>
                  <div className="row-actions">
                    <button
                      className="row-action-button detail-row-action"
                      type="button"
                      onClick={() => onOpenDetail(folder)}
                      title="Buka halaman detail parameter dan grade"
                    >
                      <FileText size={15} />
                      Detail
                    </button>
                    {lumbungUrl ? (
                      <a
                        className="row-link-button lumbung-row-action"
                        href={lumbungUrl}
                        target="_blank"
                        rel="noreferrer"
                        onClick={() => onWatchFolder(folder)}
                        title={lumbungTitle}
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
              );
            })}
          </tbody>
        </table>
      </div>
      {folders.length === 0 ? <EmptyState text="Tidak ada folder yang cocok dengan filter saat ini." /> : null}
    </section>
  );
}

function DetailPage({ detail, meta, onBack, onSync, onWatchFolder, syncing, staticSnapshot }) {
  const files = detail.files ?? [];
  const detailLumbungUrl = detail.parameter_entry_public_url || detail.public_url;
  const detailLumbungTitle = detail.parameter_entry_public_url
    ? `Buka struktur parameter ${detail.parameter_entry_detail_kode || ""} di Lumbung File`
    : "Buka folder Lumbung File";
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
        {detailLumbungUrl ? (
          <a
            className="primary-button link-button"
            href={detailLumbungUrl}
            target="_blank"
            rel="noreferrer"
            onClick={() => onWatchFolder(detail)}
            title={detailLumbungTitle}
          >
            <ExternalLink size={18} />
            Buka Struktur Parameter
          </a>
        ) : (
          <span className="disabled-link">Link tersedia setelah sinkronisasi</span>
        )}
      </div>

      <section className="file-list">
        <h3>Daftar File ({detail.file_count})</h3>
        {files.length === 0 ? (
          <EmptyState text="Belum ada evidence yang terbaca. Gunakan tombol Buka pada folder Grade untuk upload paling tepat." />
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
  const [analysisMode, setAnalysisMode] = useState("fast");
  const [candidateLimit, setCandidateLimit] = useState(20);
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
        ? await apiUpload("/api/smart-upload/recommendations", files[0], { analysis_mode: analysisMode, candidate_limit: candidateLimit })
        : await apiUploadMany("/api/smart-upload/recommendations/batch", files, { analysis_mode: analysisMode, candidate_limit: candidateLimit });
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
          <p className="eyebrow">Smart Evidence</p>
          <h2>Upload Evidence Pintar</h2>
          <p>Analisis file memakai rekomendasi lokal dan diperkuat DeepSeek V4 bila API tersedia. Pilih mode berdasarkan kebutuhan akurasi, waktu tunggu, dan estimasi biaya.</p>
        </div>
        <div className="smart-upload-mode">
          <StatusPill
            status={config?.enabled ? "Terisi Sebagian" : "Kosong"}
            explanation={config?.enabled ? "Fitur aktif, tetapi upload otomatis tetap menunggu konfirmasi." : "Fitur belum diaktifkan di environment ini."}
          />
          <small>{config?.ai_provider || "deepseek"} · {config?.ai_model || "deepseek-v4-flash"} · {config?.ai_configured ? "API tersambung" : "API belum tersambung"} · {config?.require_ai ? "AI wajib" : "hybrid"}</small>
          <button className="row-action-button" type="button" onClick={testAiConnection} disabled={aiTesting || !config?.ai_reasoning_enabled}>
            {aiTesting ? <Loader2 className="spin" size={15} /> : <Sparkles size={15} />}
            Tes AI
          </button>
        </div>
      </div>

      <AnalysisModePicker value={analysisMode} onChange={setAnalysisMode} />
      <CandidateLimitControl value={candidateLimit} onChange={setCandidateLimit} />

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
      {config && !config.ai_configured && config.require_ai ? <Notice tone="danger" text="DeepSeek API belum tersambung karena key belum terbaca di server. Mode ini mewajibkan DeepSeek V4, sehingga analisis tidak dapat dijalankan." /> : null}
      {config && !config.ai_configured && !config.require_ai ? <Notice tone="warning" text="DeepSeek API belum tersambung karena key belum terbaca di server. Mode hybrid tetap berjalan dengan rekomendasi lokal sampai key server tersedia." /> : null}
      {result ? <SmartUploadResults result={result} /> : null}
    </section>
  );
}


function AnalysisModePicker({ value, onChange }) {
  return (
    <section className="analysis-mode-panel" aria-label="Mode analisis AI">
      <div className="analysis-mode-heading">
        <div>
          <h3>Mode Analisis</h3>
          <p>Pilih seberapa banyak konteks dokumen yang dikirim ke DeepSeek V4.</p>
        </div>
        <span>{ANALYSIS_MODE_OPTIONS.find((item) => item.value === value)?.label}</span>
      </div>
      <div className="analysis-mode-grid">
        {ANALYSIS_MODE_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === value ? "analysis-mode-card active" : "analysis-mode-card"}
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


function CandidateLimitControl({ value, onChange }) {
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

function SmartUploadResults({ result }) {
  if (Array.isArray(result?.results)) {
    return (
      <section className="batch-result-panel">
        <div className="section-heading compact-heading">
          <div>
            <h3>Hasil Analisis Batch</h3>
            <p>{result.count} file selesai dianalisis sebagai file individual dan sebagai satu paket evidence.</p>
          </div>
          <div className="ai-status-box">
            <strong>Batch AI</strong>
            <span>{result.batch_ai?.status || "skipped"}</span>
          </div>
        </div>
        {result.batch_ai?.message && result.batch_ai.status !== "ok" ? <Notice tone={noticeToneForAi(result.batch_ai.status)} text={result.batch_ai.message} /> : null}
        {result.batch_analysis ? <BatchEvidencePanel analysis={result.batch_analysis} files={result.results.map((item) => item.file)} /> : null}
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

function BatchEvidencePanel({ analysis, files }) {
  const placements = analysis?.placements ?? {};
  return (
    <section className="batch-analysis-panel" aria-label="Analisis paket evidence">
      <div className="batch-analysis-main">
        <div>
          <span>Kesimpulan Paket Evidence</span>
          <strong>{analysis.package_type || "Paket Evidence SPIP"}</strong>
          <p>{analysis.summary || "AI belum memberi ringkasan paket evidence."}</p>
        </div>
        <div>
          <span>Strategi Penempatan</span>
          <p>{analysis.upload_strategy || analysis.main_conclusion || "Gunakan penempatan utama untuk upload, lalu catat rujukan pendukung bila evidence yang sama relevan lintas KK."}</p>
        </div>
      </div>
      {analysis.package_gate ? <PackageGatePanel gate={analysis.package_gate} /> : null}
      <BatchPlacementList title="Penempatan Utama Paket" placements={placements.primary ?? []} files={files} tone="primary" />
      <BatchPlacementList title="Penempatan Pendukung Paket" placements={placements.supporting ?? []} files={files} tone="supporting" />
      <BatchPlacementList title="Penempatan Lemah / Opsional Paket" placements={placements.weak ?? []} files={files} tone="weak" />
    </section>
  );
}

function BatchPlacementList({ title, placements, files, tone }) {
  if (!placements || placements.length === 0) return null;
  return (
    <div className={`placement-section placement-${tone}`}>
      <div className="placement-heading">
        <div>
          <h4>{title}</h4>
          <p>Hasil interpretasi beberapa file sebagai satu paket, bukan hanya matching per file.</p>
        </div>
        <span>{placements.length} lokasi</span>
      </div>
      <div className="placement-list">
        {placements.map((placement, index) => (
          <article className="placement-card" key={`${title}-${placement.kk_id}-${placement.detail_kode}-${placement.grade}-${index}`}>
            <div>
              <strong>{placement.kk_id || "KK"} / {placement.kode || "-"} / {placement.detail_kode || "-"} · Grade {placement.grade || "-"}</strong>
              <p>{placement.subunsur_name}</p>
              {placement.uraian ? <small>{placement.uraian}</small> : null}
            </div>
            <div className="placement-meta">
              {placement.reasoning_score !== null && placement.reasoning_score !== undefined ? <span>{Math.round(placement.reasoning_score)}%</span> : placement.confidence !== null && placement.confidence !== undefined ? <span>{Math.round(placement.confidence * 100)}%</span> : null}
              <em>{placement.candidate_status || "Paket"}</em>
            </div>
            {placement.reason ? <p className="placement-reason">{placement.reason}</p> : null}
            <div className="placement-actions">
              <span>File terkait: {relatedFileNames(placement.file_indexes, files)}</span>
              {placement.public_url ? (
                <a className="row-link-button" href={placement.public_url} target="_blank" rel="noreferrer">
                  <ExternalLink size={15} />
                  Buka Folder
                </a>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function relatedFileNames(indexes, files) {
  if (!Array.isArray(indexes) || indexes.length === 0) return "semua / belum dipetakan";
  return indexes
    .map((index) => files?.[index]?.name)
    .filter(Boolean)
    .slice(0, 4)
    .join(", ") || "belum dipetakan";
}

function PackageGatePanel({ gate }) {
  const chainLabels = {
    kebijakan: "Kebijakan",
    sosialisasi: "Sosialisasi",
    implementasi: "Implementasi",
    evaluasi: "Evaluasi",
    perbaikan: "Perbaikan",
  };
  return (
    <section className="reasoning-gate-panel package-gate-panel" aria-label="Gate paket evidence">
      <div className="reasoning-gate-main">
        <div>
          <span>Skor Paket</span>
          <strong>{Math.round(gate.score ?? 0)}%</strong>
          <small>{gate.status}</small>
        </div>
        <div>
          <span>Grade Aman Paket</span>
          <strong>{gate.safe_grade || "Belum aman"}</strong>
          <small>{gate.message}</small>
        </div>
      </div>
      <div className="chain-chip-row">
        {Object.entries(chainLabels).map(([key, label]) => (
          <span key={key} className={gate.chain?.[key] ? "chain-chip active" : "chain-chip"}>{label}</span>
        ))}
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
        {candidates.map((candidate, index) => (
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
                    disabled={!uploadAllowed || actionState !== null || !candidate.primary_allowed}
                    title={candidate.primary_allowed ? (uploadAllowed ? "Upload file sebagai penempatan utama" : "Upload belum tersedia di konfigurasi server") : "Kandidat belum melewati Reasoning Gate >80%"}
                  >
                    {actionState === `upload_primary:${index}` ? <Loader2 className="spin" size={15} /> : <CheckCircle2 size={15} />}
                    Upload Utama
                  </button>
                </div>
              </div>
            </div>
          </article>
        ))}
      </div>
      {candidates.length === 0 ? <EmptyState text={result.ai?.status === "ok" ? "Belum ada kandidat yang cukup kuat dari DeepSeek V4." : "DeepSeek V4 belum berhasil merespons, sehingga aplikasi tidak menampilkan rekomendasi lokal."} /> : null}
    </section>
  );
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
          const buttonAllowed = actionAllowed && gateAllowed;
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
                      title={buttonAllowed ? `${uploadLabel} untuk folder ini` : actionType === "upload_primary" ? "Belum melewati Reasoning Gate >80% atau upload dikunci" : "Aksi belum tersedia"}
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
            <GradeRecommendation recommendation={grade.recommendation} />
            <GradeEvidenceFolder folder={grade.evidence_folders?.[0]} />
          </div>
        </article>
      ))}
    </div>
  );
}

function GradeRecommendation({ recommendation }) {
  if (!recommendation) return null;

  return (
    <details className="grade-recommendation">
      <summary>
        <span>
          <Sparkles size={15} />
          Rekomendasi
        </span>
        <strong>{recommendation.summary}</strong>
      </summary>
      <div className="recommendation-body">
        <RecommendationList title="File Utama" items={recommendation.primary_files} />
        <RecommendationList title="File Pendukung" items={recommendation.supporting_files} />
        <RecommendationList title="Contoh Nama File" items={recommendation.example_filenames} />
        <RecommendationList title="Rantai Bukti" items={recommendation.evidence_chain} emphasis />
        {recommendation.warning ? (
          <p className="recommendation-warning">{recommendation.warning}</p>
        ) : null}
        <RecommendationList title="Belum Cukup Jika Hanya" items={recommendation.not_sufficient} muted />
      </div>
    </details>
  );
}

function RecommendationList({ title, items, emphasis = false, muted = false }) {
  const safeItems = (items ?? []).filter(Boolean);
  if (safeItems.length === 0) return null;

  return (
    <div className={`recommendation-list${emphasis ? " recommendation-list-emphasis" : ""}${muted ? " recommendation-list-muted" : ""}`}>
      <span>{title}</span>
      <ul>
        {safeItems.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
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



function clampCandidateLimit(value) {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) return 1;
  return Math.max(1, Math.min(100, parsed));
}

function formatNumber(value) {
  return new Intl.NumberFormat("id-ID").format(Math.round(Number(value) || 0));
}

function formatUsdRange(value) {
  if (!value?.low && !value?.high) return "Belum tersedia";
  const low = Number(value.low || 0);
  const high = Number(value.high || low);
  if (low === high) return `$${low.toFixed(7)}`;
  return `$${low.toFixed(7)}-${high.toFixed(7)}`;
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
