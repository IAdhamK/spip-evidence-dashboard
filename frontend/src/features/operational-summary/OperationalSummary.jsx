import { ArrowRight, FileClock } from "lucide-react";
import { formatBytes, formatDate } from "../../lib/formatters.js";
import {
  operationalFolderLabel,
  operationalSummaryView,
} from "./operational-summary.js";


export default function OperationalSummary({ dashboard, onOpenDetail, onOpenRecentProgress }) {
  const summary = operationalSummaryView(dashboard);
  if (!summary.available || !summary.latestDocuments.length) return null;

  return (
    <section className="latest-documents-panel" aria-labelledby="latest-documents-title">
      <header className="latest-documents-header">
        <div className="latest-documents-title">
          <span className="latest-documents-icon" aria-hidden="true"><FileClock size={21} /></span>
          <div>
            <p className="eyebrow">Berdasarkan tanggal perubahan file</p>
            <h2 id="latest-documents-title">Dokumen Terbaru</h2>
          </div>
        </div>
        <div className="latest-documents-actions">
          <span className="latest-documents-checked">Diperiksa {formatDate(summary.lastCheckedAt)}</span>
          <button className="latest-documents-all" type="button" onClick={onOpenRecentProgress}>
            Lihat semua progres
            <ArrowRight size={16} />
          </button>
        </div>
      </header>

      <div className="latest-documents-grid">
        {summary.latestDocuments.slice(0, 4).map((item) => (
          <article className="latest-document-card" key={`${item.id}-${item.kk_id}-${item.kode}`}>
            <div className="latest-document-copy">
              <strong title={item.base_name}>{item.base_name}</strong>
              <span>{operationalFolderLabel(item)} · {formatDate(item.modified_at)}</span>
              <small>{item.subunsur_name} · {formatBytes(item.size_bytes)}</small>
            </div>
            <button
              className="latest-document-button"
              type="button"
              onClick={() => onOpenDetail({ kk_id: item.kk_id, kode: item.kode })}
              aria-label={`Buka detail ${operationalFolderLabel(item)}`}
            >
              Detail
              <ArrowRight size={15} />
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
