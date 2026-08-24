import { AlertTriangle, CheckCircle2, FileSpreadsheet, Info, XCircle } from "lucide-react";
import { workbookEvidenceSummary } from "./workbook-evidence.js";

function ToneIcon({ tone }) {
  if (tone === "success") return <CheckCircle2 size={17} aria-hidden="true" />;
  if (tone === "warning") return <AlertTriangle size={17} aria-hidden="true" />;
  if (tone === "danger") return <XCircle size={17} aria-hidden="true" />;
  return <Info size={17} aria-hidden="true" />;
}

function ParameterIdentity({ parameter, title, fallbackText }) {
  if (!parameter) return <p className="workbook-empty-parameter">{fallbackText}</p>;
  const existingGrade = parameter.existing_grade_assessment?.candidate_grade;
  return (
    <article className="workbook-parameter-item">
      <span>{title}</span>
      <strong>{parameter.kk_id || "KK belum diketahui"} · {parameter.kode || "-"} · {parameter.detail_kode || "-"}</strong>
      <p>{parameter.uraian || "Uraian parameter belum tersedia"}</p>
      <small>{parameter.sourceLabel}</small>
      {existingGrade ? <em>Grade existing — belum resmi: Grade {existingGrade}</em> : null}
    </article>
  );
}

function SheetCard({ sheet, index, onOpenDetails }) {
  const primary = sheet.primaryParameter;
  return (
    <details className={`workbook-sheet-card workbook-tone-${sheet.tone}`} defaultOpen={index === 0}>
      <summary>
        <ToneIcon tone={sheet.tone} />
        <span className="workbook-sheet-heading"><strong>{sheet.sheetName}</strong><small>{sheet.statusLabel} · {sheet.evidenceRoleLabel}</small></span>
        <span className="workbook-sheet-primary"><strong>{primary ? `${primary.kk_id} · ${primary.kode} · ${primary.detail_kode}` : "Parameter belum ditemukan"}</strong><small>{primary ? `Confidence ${Math.round(primary.mappingScore * 100)}%` : `${sheet.warnings.length} peringatan`}</small></span>
      </summary>
      <div className="workbook-sheet-content">
        <ParameterIdentity parameter={primary} title="Parameter utama" fallbackText="Sheet ini belum mempunyai parameter utama yang aman untuk ditampilkan." />
        {sheet.secondaryParameters.length ? (
          <div className="workbook-secondary-list">
            {sheet.secondaryParameters.map((parameter) => <ParameterIdentity key={`${parameter.kk_id}-${parameter.kode}-${parameter.detail_kode}`} parameter={parameter} title="Parameter sekunder" />)}
          </div>
        ) : null}
        <div className="workbook-sheet-metrics">
          <div><span>Fakta sumber</span><strong>{Number(sheet.fact_count || 0)}</strong></div>
          <div><span>Coverage</span><strong>{sheet.coverageLabel}</strong></div>
          <div><span>Verifikasi</span><strong>{primary?.verificationLabel || "Belum tersedia"}</strong></div>
        </div>
        {sheet.warnings.length ? <div className="workbook-sheet-warnings"><strong>Yang perlu diperhatikan</strong><ul>{sheet.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div> : null}
        {typeof onOpenDetails === "function" ? <button className="row-action-button workbook-detail-button" type="button" onClick={onOpenDetails}>Buka Detail Pemeriksaan</button> : null}
      </div>
    </details>
  );
}

export default function WorkbookEvidencePanel({ evidence, onOpenDetails }) {
  const model = workbookEvidenceSummary(evidence);
  if (!model.showPanel) return null;
  return (
    <section className="workbook-evidence-panel" aria-label="Workbook Multi-Evidence">
      <div className="workbook-evidence-heading">
        <div><span className="workbook-evidence-icon"><FileSpreadsheet size={20} aria-hidden="true" /></span><div><h4>Workbook Multi-Evidence</h4><p>{model.sheetCount} sheet dianalisis · {model.substantiveSheetCount} sheet substantif · {model.parameterCount} parameter ditemukan</p></div></div>
        <span className="workbook-multi-badge">Multi-evidence terdeteksi</span>
      </div>

      {model.warnings.length ? <div className="workbook-warning-summary"><AlertTriangle size={18} aria-hidden="true" /><div><strong>Peringatan penting</strong><ul>{model.warnings.slice(0, 5).map((warning) => <li key={warning}>{warning}</li>)}</ul></div></div> : null}

      <div className="workbook-review-notice"><Info size={18} aria-hidden="true" /><span>{model.review.message}</span></div>

      {model.parameterSources.length ? (
        <details className="workbook-aggregate" open>
          <summary>Ringkasan parameter seluruh workbook</summary>
          <div className="workbook-aggregate-list">
            {model.parameterSources.map((parameter) => (
              <article key={`${parameter.kk_id}-${parameter.kode}-${parameter.detail_kode}`}>
                <span>{parameter.workbookRole === "primary" ? "Parameter utama workbook" : "Parameter sekunder workbook"}</span>
                <strong>{parameter.kk_id || "KK belum diketahui"} · {parameter.kode || "-"} · {parameter.detail_kode || "-"}</strong>
                <p>{parameter.uraian || "Uraian parameter belum tersedia"}</p>
                <small>{parameter.sourceLabel}</small>
              </article>
            ))}
          </div>
        </details>
      ) : null}

      <div className="workbook-sheet-list">
        {model.evidenceSheets.map((sheet, index) => <SheetCard key={sheet.key} sheet={sheet} index={index} onOpenDetails={onOpenDetails} />)}
      </div>

      {model.excludedSheets.length ? (
        <details className="workbook-excluded-sheets">
          <summary>{model.excludedSheets.length} sheet tidak digunakan sebagai evidence utama</summary>
          <ul>{model.excludedSheets.map((sheet) => <li key={sheet.key}><strong>{sheet.sheetName}</strong><span>{sheet.statusLabel}{sheet.warnings[0] ? ` · ${sheet.warnings[0]}` : ""}</span></li>)}</ul>
        </details>
      ) : null}
    </section>
  );
}
