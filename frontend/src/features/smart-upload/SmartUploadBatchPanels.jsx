import { ExternalLink } from "lucide-react";


export default function BatchEvidencePanel({ analysis, files }) {
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
