import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Database,
  Eye,
  ExternalLink,
  FileArchive,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Loader2,
  ListChecks,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { apiGet, apiPost, isStaticSnapshot } from "./lib/api.js";
import { formatBytes, formatDate } from "./lib/formatters.js";
import { canonicalLumbungUrl } from "./lib/lumbung-link.js";
import { EmptyState, Notice } from "./features/shared/Feedback.jsx";
import { StatusPill, Tooltip } from "./features/shared/StatusPill.jsx";
import GuidedReviewPage from "./features/GuidedReviewPage.jsx";
import VisualReviewPage from "./features/VisualReviewPage.jsx";
import GovernancePage from "./features/GovernancePage.jsx";
import SmartUploadPage from "./features/SmartUploadPage.jsx";
import "./styles/main.css";

const STATUS_ORDER = ["Kosong", "Terisi Sebagian", "Terisi", "Perlu Kurasi", "Final"];
function parseRouteHash() {
  if (typeof window === "undefined") return { page: "dashboard" };
  const hash = window.location.hash || "";
  if (hash === "#/smart-upload") return { page: "smart-upload" };
  if (hash === "#/guided-review") return { page: "guided-review" };
  if (hash === "#/visual-review") return { page: "visual-review" };
  if (hash === "#/governance") return { page: "governance" };
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
  } else if (route?.page === "guided-review") {
    nextHash = "#/guided-review";
  } else if (route?.page === "visual-review") {
    nextHash = "#/visual-review";
  } else if (route?.page === "governance") {
    nextHash = "#/governance";
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
  const [guidedReviewOpen, setGuidedReviewOpen] = useState(false);
  const [visualReviewOpen, setVisualReviewOpen] = useState(false);
  const [governanceOpen, setGovernanceOpen] = useState(false);
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
    setGuidedReviewOpen(false);
    setVisualReviewOpen(false);
    setGovernanceOpen(false);
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
      setGuidedReviewOpen(false);
      setGovernanceOpen(false);
      setVisualReviewOpen(false);
      setSmartUploadOpen(true);
      return;
    }
    if (route.page === "guided-review") {
      setSelected(null);
      setDetailLoading(false);
      setSmartUploadOpen(false);
      setGovernanceOpen(false);
      setVisualReviewOpen(false);
      setGuidedReviewOpen(true);
      return;
    }
    if (route.page === "visual-review") {
      setSelected(null);
      setDetailLoading(false);
      setSmartUploadOpen(false);
      setGuidedReviewOpen(false);
      setGovernanceOpen(false);
      setVisualReviewOpen(true);
      return;
    }
    if (route.page === "governance") {
      setSelected(null);
      setDetailLoading(false);
      setSmartUploadOpen(false);
      setGuidedReviewOpen(false);
      setVisualReviewOpen(false);
      setGovernanceOpen(true);
      return;
    }
    if (route.page === "detail") {
      setSmartUploadOpen(false);
      setGuidedReviewOpen(false);
      setVisualReviewOpen(false);
      setGovernanceOpen(false);
      await openDetail({ kk_id: route.kkId, kode: route.kode }, { updateRoute: false });
      return;
    }
    setSelected(null);
    setDetailLoading(false);
    setSmartUploadOpen(false);
    setGuidedReviewOpen(false);
    setVisualReviewOpen(false);
    setGovernanceOpen(false);
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
    const handleLumbungClick = (event) => {
      const link = event.target?.closest?.("a[href*='lumbungfile.kemendesa.go.id']");
      if (!link) return;
      const fixedUrl = canonicalLumbungUrl(link.href);
      if (fixedUrl && fixedUrl !== link.href) {
        link.href = fixedUrl;
      }
    };
    document.addEventListener("click", handleLumbungClick, true);
    return () => document.removeEventListener("click", handleLumbungClick, true);
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
                setGuidedReviewOpen(false);
                setVisualReviewOpen(false);
                setGovernanceOpen(false);
                setSmartUploadOpen(true);
                updateRouteHash({ page: "smart-upload" });
              }}
              title="Buka halaman rekomendasi folder evidence berbasis knowledge base"
            >
              <Sparkles size={18} />
              Upload Pintar
            </button>
          ) : null}
          {smartUploadConfig?.analysis_pipeline_v2_enabled ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setSelected(null);
                setSmartUploadOpen(false);
                setVisualReviewOpen(false);
                setGovernanceOpen(false);
                setGuidedReviewOpen(true);
                updateRouteHash({ page: "guided-review" });
              }}
              title="Review dokumen satu per satu tanpa kode teknis"
            >
              <ListChecks size={18} />
              Review Terpandu
            </button>
          ) : null}
          {smartUploadConfig?.analysis_pipeline_v2_enabled ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setSelected(null);
                setSmartUploadOpen(false);
                setGuidedReviewOpen(false);
                setGovernanceOpen(false);
                setVisualReviewOpen(true);
                updateRouteHash({ page: "visual-review" });
              }}
              title="Periksa gambar OCR yang makna visualnya masih tertahan"
            >
              <Eye size={18} />
              Review Visual
            </button>
          ) : null}
          {smartUploadConfig?.analysis_pipeline_v2_enabled ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setSelected(null);
                setSmartUploadOpen(false);
                setGuidedReviewOpen(false);
                setVisualReviewOpen(false);
                setGovernanceOpen(true);
                updateRouteHash({ page: "governance" });
              }}
              title="Review rule domain dan consent OCR/vision tanpa kode"
            >
              <ShieldCheck size={18} />
              Governance V2
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
      ) : governanceOpen ? (
        <GovernancePage
          onBack={() => {
            setGovernanceOpen(false);
            updateRouteHash({ page: "dashboard" });
          }}
        />
      ) : visualReviewOpen ? (
        <VisualReviewPage
          onBack={() => {
            setVisualReviewOpen(false);
            updateRouteHash({ page: "dashboard" });
          }}
        />
      ) : guidedReviewOpen ? (
        <GuidedReviewPage
          onBack={() => {
            setGuidedReviewOpen(false);
            updateRouteHash({ page: "dashboard" });
          }}
        />
      ) : smartUploadOpen ? (
        <SmartUploadPage
          onBack={() => {
            setSmartUploadOpen(false);
            updateRouteHash({ page: "dashboard" });
          }}
          onOpenGuidedReview={() => {
            setSmartUploadOpen(false);
            setVisualReviewOpen(false);
            setGuidedReviewOpen(true);
            updateRouteHash({ page: "guided-review" });
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
              const rawLumbungUrl = folder.parameter_entry_public_url || folder.public_url;
              const lumbungUrl = canonicalLumbungUrl(
                rawLumbungUrl,
                folder.parameter_entry_folder_path || folder.folder_path,
              );
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
  const detailLumbungUrl = canonicalLumbungUrl(
    detail.parameter_entry_public_url || detail.public_url,
    detail.parameter_entry_folder_path || detail.folder_path,
  );
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
        <a href={canonicalLumbungUrl(folder.public_url, folder.folder_path)} target="_blank" rel="noreferrer" title="Buka folder grade di Lumbung File">
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

createRoot(document.getElementById("root")).render(<App />);
