import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { formatNumber } from "../../lib/formatters.js";


export function aggregateLinkCrawlFromResult(result) {
  const items = Array.isArray(result?.results)
    ? result.results.map((item) => item.link_crawl).filter(Boolean)
    : result?.link_crawl
      ? [result.link_crawl]
      : [];
  if (items.length === 0) return null;
  const total = items.reduce((sum, item) => sum + (item.total || 0), 0);
  if (!total) return null;
  return items.reduce((acc, item) => ({
    total: acc.total + (item.total || 0),
    cached_ok: acc.cached_ok + (item.cached_ok || 0),
    pending: acc.pending + (item.pending || 0),
    fetching: acc.fetching + (item.fetching || 0),
    error: acc.error + (item.error || 0),
    unsupported: acc.unsupported + (item.unsupported || 0),
    truncated: acc.truncated + (item.truncated || 0),
    linked_text_char_count: acc.linked_text_char_count + (item.linked_text_char_count || 0),
    needs_crawl: acc.needs_crawl || Boolean(item.needs_crawl),
  }), {
    total: 0,
    cached_ok: 0,
    pending: 0,
    fetching: 0,
    error: 0,
    unsupported: 0,
    truncated: 0,
    linked_text_char_count: 0,
    needs_crawl: false,
  });
}

export default function LinkCrawlPanel({ linkCrawl, globalStatus, busy, canReanalyze, onStartCrawl, onReanalyze }) {
  if (!linkCrawl?.total) return null;
  const cached = linkCrawl.cached_ok || 0;
  const pending = (linkCrawl.pending || 0) + (linkCrawl.fetching || 0);
  const failed = (linkCrawl.error || 0) + (linkCrawl.unsupported || 0);
  const readyPercent = linkCrawl.total ? Math.round((cached / linkCrawl.total) * 100) : 0;
  const isRunning = Boolean(globalStatus?.running);
  const canStart = Boolean(onStartCrawl) && (linkCrawl.needs_crawl || pending > 0 || cached < linkCrawl.total);
  const canRerun = Boolean(onReanalyze) && Boolean(canReanalyze) && cached > 0 && !isRunning && pending === 0;
  const message = isRunning
    ? "Crawler sedang membaca link evidence di background. User tetap bisa melihat hasil awal, lalu ulangi analisis setelah proses selesai."
    : cached > 0
      ? `Isi ${cached} link evidence sudah tersedia di cache. Jalankan analisis ulang agar lampiran ikut dihitung dalam rekomendasi.`
      : pending > 0
        ? "Link evidence masuk antrean pembacaan background. Jalankan analisis ulang setelah cache selesai agar isi lampiran ikut dibaca."
        : "Link evidence terdeteksi, tetapi belum ada isi link yang terbaca.";
  return (
    <section className={`link-crawl-panel${isRunning ? " running" : ""}`} aria-label="Status pembacaan link evidence">
      <div className="link-crawl-main">
        <span>Link Evidence</span>
        <strong>{cached} / {linkCrawl.total} terbaca</strong>
        <p>{message}</p>
      </div>
      <div className="link-crawl-stats">
        <span>{isRunning ? "Crawler aktif" : "Crawler siap"}</span>
        <span>Antrean {pending}</span>
        <span>Kendala {failed}</span>
        {linkCrawl.linked_text_char_count ? <span>{formatNumber(linkCrawl.linked_text_char_count)} karakter cache</span> : null}
        {linkCrawl.truncated ? <span>{linkCrawl.truncated} link belum dimasukkan antrean</span> : null}
      </div>
      <div className="link-crawl-actions">
        <button
          className="link-crawl-button secondary"
          type="button"
          onClick={onStartCrawl}
          disabled={!canStart || busy || isRunning}
        >
          {busy || isRunning ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
          Baca Link Evidence
        </button>
        <button
          className="link-crawl-button"
          type="button"
          onClick={onReanalyze}
          disabled={!canRerun || busy}
          title={cached > 0 ? "Jalankan ulang analisis memakai isi link yang sudah terbaca." : "Tombol aktif setelah minimal satu link evidence terbaca."}
        >
          {busy ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}
          Analisis Ulang dengan Link Terbaca
        </button>
      </div>
      <div className="link-crawl-progress" aria-hidden="true">
        <i style={{ width: `${Math.max(6, readyPercent)}%` }} />
      </div>
      {globalStatus?.last_message ? <small className="link-crawl-status">{globalStatus.last_message}</small> : null}
    </section>
  );
}
