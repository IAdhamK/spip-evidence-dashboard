import { Loader2, RefreshCw } from "lucide-react";

function percentage(value) {
  return value == null ? "-" : `${Math.round(value * 1000) / 10}%`;
}

export default function ShadowComparisonCard({ report, refreshing, onRefresh }) {
  const shadow = report ?? {};
  return (
    <article className="release-step-card">
      <div className="release-step-heading">
        <span>1</span>
        <div>
          <h3>Periksa shadow comparison V1 dan V2</h3>
          <p>Setiap legacy review dalam shadow mode dipasangkan otomatis dengan run V2. Report hanya memuat kode parameter dan metrik agregat, bukan isi dokumen.</p>
        </div>
      </div>
      <div className="release-dataset-status">
        <div><strong>{shadow.completed_count || 0} / 50 terminal</strong><span>{shadow.queued_count || 0} menunggu · {shadow.failed_count || 0} gagal/cancelled</span></div>
        <div><strong>Top-1 {percentage(shadow.top_1_match_rate)}</strong><span>Exact set {percentage(shadow.exact_set_match_rate)} · bukan pengganti expert gold</span></div>
        <div><strong>{shadow.report_sha256 ? "Checksum tersedia" : "Belum ada pasangan"}</strong><span>{shadow.report_sha256 ? `${shadow.report_sha256.slice(0, 16)}…` : "Aktifkan shadow mode untuk mulai"}</span></div>
      </div>
      <button className="secondary-button" type="button" disabled={refreshing} onClick={onRefresh}>
        {refreshing ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />}
        Muat Ulang Shadow Ledger
      </button>
      {!shadow.review_target_reached ? <div className="notice notice-warning">Keputusan passed tetap ditahan sampai minimal 50 pasangan terminal. Saat ini {shadow.completed_count || 0}/50.</div> : null}
    </article>
  );
}
