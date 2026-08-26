import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  FileClock,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";
import { apiGet } from "../../lib/api.js";
import { formatBytes, formatDate } from "../../lib/formatters.js";
import { Notice } from "../shared/Feedback.jsx";
import { operationalFolderLabel } from "./operational-summary.js";
import { filterRecentProgress, recentProgressCounts } from "./recent-progress.js";


export default function RecentProgressPage({ dashboard, onBack, onOpenDetail }) {
  const fallbackDocuments = dashboard?.operational_summary?.latest_documents ?? [];
  const [documents, setDocuments] = useState(fallbackDocuments);
  const [lastCheckedAt, setLastCheckedAt] = useState(dashboard?.operational_summary?.last_checked_at ?? null);
  const [query, setQuery] = useState("");
  const [kkId, setKkId] = useState("Semua");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadProgress() {
    setLoading(true);
    setError("");
    try {
      const result = await apiGet("/api/operational-progress?limit=100");
      setDocuments(result?.latest_documents ?? []);
      setLastCheckedAt(result?.last_checked_at ?? null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadProgress();
  }, []);

  const filtered = useMemo(
    () => filterRecentProgress(documents, { query, kkId }),
    [documents, query, kkId],
  );
  const counts = useMemo(() => recentProgressCounts(documents), [documents]);

  return (
    <section className="recent-progress-page">
      <button className="back-button" type="button" onClick={onBack}>
        <ArrowLeft size={18} />
        Kembali ke Dashboard
      </button>

      <header className="recent-progress-header">
        <div className="recent-progress-heading">
          <span className="recent-progress-heading-icon" aria-hidden="true"><FileClock size={28} /></span>
          <div>
            <p className="eyebrow">Aktivitas metadata Lumbung File</p>
            <h2>Progres Dokumen Terbaru</h2>
            <p>Daftar dokumen berdasarkan tanggal perubahan terbaru yang terbaca saat sinkronisasi.</p>
          </div>
        </div>
        <div className="recent-progress-refresh">
          <span>Diperiksa {formatDate(lastCheckedAt)}</span>
          <button className="secondary-button" type="button" onClick={loadProgress} disabled={loading}>
            {loading ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />}
            Perbarui daftar
          </button>
        </div>
      </header>

      <div className="recent-progress-stats" aria-label="Jumlah dokumen terbaru per KK">
        <div><span>Ditampilkan</span><strong>{documents.length}</strong></div>
        {Object.entries(counts).map(([key, count]) => (
          <div key={key}><span>{key}</span><strong>{count}</strong></div>
        ))}
      </div>

      <div className="recent-progress-controls">
        <label className="search-box">
          <Search size={18} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Cari nama dokumen, subunsur, kode, atau KK"
            aria-label="Cari progres dokumen terbaru"
          />
        </label>
        <label className="recent-progress-filter">
          <span>Kelompok KK</span>
          <select value={kkId} onChange={(event) => setKkId(event.target.value)}>
            <option value="Semua">Semua KK</option>
            {Object.keys(counts).map((key) => <option value={key} key={key}>{key}</option>)}
          </select>
        </label>
      </div>

      {error ? <Notice tone="danger" text={error} /> : null}

      <section className="recent-progress-results" aria-live="polite">
        <div className="recent-progress-results-heading">
          <div>
            <h3>Riwayat Terbaru</h3>
            <p>{filtered.length} dokumen sesuai pencarian dan filter.</p>
          </div>
          <span>Terbaru → terlama</span>
        </div>

        {loading && !documents.length ? (
          <div className="loading-state"><Loader2 className="spin" size={24} /> Memuat progres terbaru...</div>
        ) : filtered.length ? (
          <div className="recent-progress-list">
            {filtered.map((item, index) => (
              <article className="recent-progress-row" key={`${item.id}-${item.kk_id}-${item.kode}-${index}`}>
                <span className="recent-progress-number">{index + 1}</span>
                <div className="recent-progress-copy">
                  <strong>{item.base_name}</strong>
                  <span>{operationalFolderLabel(item)} · {item.subunsur_name}</span>
                  <small>Diubah {formatDate(item.modified_at)} · {formatBytes(item.size_bytes)}</small>
                </div>
                <button
                  className="recent-progress-detail"
                  type="button"
                  onClick={() => onOpenDetail({ kk_id: item.kk_id, kode: item.kode })}
                >
                  Lihat Subunsur
                  <ArrowRight size={16} />
                </button>
              </article>
            ))}
          </div>
        ) : (
          <div className="recent-progress-empty">Belum ada dokumen yang sesuai dengan pencarian ini.</div>
        )}
      </section>
    </section>
  );
}
