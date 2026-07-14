import { useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { apiGet, apiPost } from "../../lib/api.js";

export function V2PackageControl({ runIds }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Review dan approve mapping tiap run sebelum membuat paket.");
  const [analysis, setAnalysis] = useState(null);

  async function createPackage() {
    setBusy(true);
    setMessage("");
    try {
      const snapshots = await Promise.all(runIds.map((runId) => apiGet(`/api/analysis-runs/${runId}`)));
      const ineligible = snapshots.filter((item) => !["approved", "uploaded"].includes(item.run?.status));
      if (ineligible.length) throw new Error(`${ineligible.length} run belum approved; paket tidak akan mencampur kandidat yang belum diverifikasi.`);
      const packageResult = await apiPost("/api/analysis-packages", {
        name: `Paket Evidence ${new Date().toLocaleDateString("id-ID")}`,
        run_ids: runIds,
      });
      setAnalysis(packageResult);
      setMessage("Paket dibentuk hanya dari mapping human-approved dan fully verified.");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="batch-analysis-panel">
      <div className="placement-heading">
        <div><h4>Cross-document Package</h4><p>{message}</p></div>
        <button className="row-action-button" type="button" disabled={busy} onClick={createPackage}>{busy ? <Loader2 className="spin" size={16} /> : <ShieldCheck size={16} />}Verifikasi dan Bentuk Paket</button>
      </div>
      {analysis ? <CrossDocumentPackagePanel analysis={analysis} /> : null}
    </section>
  );
}

export function CrossDocumentPackagePanel({ analysis }) {
  const assessments = analysis?.assessments ?? [];
  const chainLabels = { policy: "Kebijakan", socialization: "Sosialisasi", implementation: "Implementasi", evaluation: "Evaluasi", improvement: "Perbaikan" };
  return (
    <section className="batch-analysis-panel" aria-label="Cross-document evidence graph">
      <div className="batch-analysis-main">
        <div><span>Cross-document Synthesis</span><strong>{analysis.name}</strong><p>{analysis.members?.length ?? 0} run digabung hanya pada KK, parameter, organisasi, dan periode yang kompatibel.</p></div>
        <div><span>Status Paket</span><strong>{analysis.status}</strong><p>Primary upload: {analysis.primary_blocked ? "diblokir" : "diizinkan"}</p></div>
      </div>
      {analysis.block_reasons?.length ? <div className="gate-warning-list">{analysis.block_reasons.map((item) => <span key={item}>{item}</span>)}</div> : null}
      <div className="placement-list">
        {assessments.map((item) => (
          <article className="placement-card" key={item.id}>
            <div><strong>{item.kk_id} / {item.kode} / {item.detail_kode}</strong><p>{item.organization} · {item.period}</p></div>
            <div className="placement-meta"><span>Grade aman {item.safe_grade || "-"}</span><em>{item.status}</em></div>
            <div className="chain-chip-row">{Object.entries(chainLabels).map(([key, label]) => <span key={key} className={item.chain?.[key] ? "chain-chip active" : "chain-chip"}>{label}</span>)}</div>
            <small>Run terkait: {(item.supporting_run_ids ?? []).map((id) => `#${id}`).join(", ") || "-"}</small>
            {item.contradictions?.length ? <div className="gate-warning-list compact">{item.contradictions.map((warning) => <span key={warning}>{warning}</span>)}</div> : null}
          </article>
        ))}
      </div>
    </section>
  );
}
