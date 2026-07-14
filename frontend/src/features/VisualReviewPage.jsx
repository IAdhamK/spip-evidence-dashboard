import { useEffect, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Eye,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { apiGet, apiPost } from "../lib/api.js";
import { EmptyState, Notice } from "./shared/Feedback.jsx";

const VISUAL_REVIEW_OUTCOMES = [
  {
    value: "confirmed",
    label: "Teks dan makna benar",
    description: "Teks OCR serta konteks visual sudah cocok dengan gambar sumber.",
  },
  {
    value: "corrected",
    label: "Perlu koreksi",
    description: "Tuliskan teks atau deskripsi visual yang benar setelah melihat gambar.",
  },
  {
    value: "not_evidence",
    label: "Bukan evidence",
    description: "Gambar sudah diperiksa tetapi tidak merupakan evidence SPIP.",
  },
  {
    value: "unsure",
    label: "Belum yakin",
    description: "Pertahankan fail-closed dan minta pemeriksaan orang lain.",
  },
];

const VISUAL_REGION_TYPES = [
  { value: "picture", label: "Gambar/foto" },
  { value: "chart", label: "Chart/grafik" },
  { value: "diagram", label: "Diagram/alur" },
  { value: "signature", label: "Tanda tangan" },
  { value: "stamp", label: "Stempel/cap" },
  { value: "table", label: "Tabel visual" },
  { value: "other", label: "Visual lainnya" },
];

function visualSourceLabel(unit) {
  const location = unit?.source_location ?? {};
  if (location.sheet) return `Sheet ${location.sheet}`;
  if (location.rendered_page) return `Halaman visual ${location.rendered_page}`;
  if (location.slide) return `Slide ${location.slide}`;
  if (location.page) return `Halaman ${location.page}`;
  if (location.image) return `Gambar ${location.image}`;
  return unit?.unit_key || "Lokasi sumber";
}

function normalizedSemanticRegions(unit) {
  return (unit?.metadata?.semantic_regions ?? []).filter((region) => {
    const box = region?.bbox;
    return region?.coordinate_space === "normalized_top_left"
      && [box?.x, box?.y, box?.width, box?.height].every((value) => Number.isFinite(Number(value)))
      && Number(box.width) > 0
      && Number(box.height) > 0;
  }).map((region, index) => {
    const box = region.bbox;
    const clamp = (value) => Math.max(0, Math.min(1, Number(value)));
    const x = clamp(box.x);
    const y = clamp(box.y);
    return {
      ...region,
      overlayKey: `${region.region_type || "visual"}-${index}`,
      overlayStyle: {
        left: `${x * 100}%`,
        top: `${y * 100}%`,
        width: `${Math.max(0, Math.min(1 - x, Number(box.width))) * 100}%`,
        height: `${Math.max(0, Math.min(1 - y, Number(box.height))) * 100}%`,
      },
    };
  });
}


export default function VisualReviewPage({ onBack }) {
  const [reviewStatus, setReviewStatus] = useState("all");
  const [queue, setQueue] = useState({ items: [], counts: {}, kind_counts: {}, total: 0 });
  const [currentIndex, setCurrentIndex] = useState(0);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reviewerId, setReviewerId] = useState(() => window.localStorage.getItem("visual-reviewer-id") || "");
  const [form, setForm] = useState({
    decision: "",
    reviewedText: "",
    semanticDescription: "",
    semanticRegions: [],
    regionType: "diagram",
    regionLabel: "",
    reason: "",
    attested: false,
  });
  const [formKey, setFormKey] = useState("");
  const [applyReason, setApplyReason] = useState("Terapkan keputusan review visual/OCR yang sudah diperiksa pada run turunan.");
  const [applyAttested, setApplyAttested] = useState(false);
  const [action, setAction] = useState("");
  const [regionMarking, setRegionMarking] = useState(false);
  const [regionStart, setRegionStart] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const items = queue.items ?? [];
  const current = items[currentIndex] ?? null;
  const semanticRegions = normalizedSemanticRegions({
    metadata: {
      semantic_regions: [
        ...(detail?.unit?.metadata?.semantic_regions ?? []),
        ...(form.semanticRegions ?? []),
      ],
    },
  });

  function defaultVisualReason(decision) {
    return {
      confirmed: "Teks OCR dan makna visual sesuai setelah gambar sumber diperiksa.",
      corrected: "Teks atau deskripsi visual dikoreksi berdasarkan gambar sumber.",
      not_evidence: "Gambar telah diperiksa dan bukan evidence SPIP.",
      unsure: "Makna visual belum cukup jelas untuk dipastikan.",
    }[decision] || "";
  }

  async function loadQueue({ afterSave = false } = {}) {
    const data = await apiGet(`/api/analysis-runs/visual-review/queue?review_status=${reviewStatus}&limit=500`);
    setQueue(data);
    const nextItems = data.items ?? [];
    if (!nextItems.length) {
      setCurrentIndex(0);
      setDetail(null);
      return data;
    }
    if (afterSave) {
      const nextPending = nextItems.findIndex((item) => item.review_state !== "reviewed");
      setCurrentIndex(nextPending >= 0 ? nextPending : Math.min(currentIndex, nextItems.length - 1));
    } else {
      const remembered = Number(window.localStorage.getItem("visual-review-last-unit-id") || 0);
      const rememberedIndex = nextItems.findIndex((item) => item.id === remembered);
      setCurrentIndex(rememberedIndex >= 0 ? rememberedIndex : Math.min(currentIndex, nextItems.length - 1));
    }
    return data;
  }

  async function loadDetail(item) {
    if (!item) return;
    setDetailLoading(true);
    setError("");
    try {
      const data = await apiGet(
        `/api/analysis-runs/visual-review/${item.run_id}/${encodeURIComponent(item.unit_key)}`,
      );
      const latest = data.latest_decision;
      let initial = {
        decision: latest?.decision || "",
        reviewedText: latest?.reviewed_text || data.review_text || "",
        semanticDescription: latest?.semantic_description || "",
        semanticRegions: latest?.evidence?.reviewed_semantic_regions || [],
        regionType: "diagram",
        regionLabel: "",
        reason: latest?.reason || "",
        attested: false,
      };
      const key = `${item.run_id}:${item.unit_key}`;
      try {
        const draft = JSON.parse(window.localStorage.getItem(`visual-review-draft-${key}`) || "null");
        if (draft && typeof draft === "object") initial = { ...initial, ...draft };
      } catch {
        // Draft browser yang rusak tidak mengganti keputusan server.
      }
      setDetail(data);
      setForm(initial);
      setRegionMarking(false);
      setRegionStart(null);
      setFormKey(key);
      if (latest?.reviewer_id && !reviewerId) setReviewerId(latest.reviewer_id);
      window.localStorage.setItem("visual-review-last-unit-id", String(item.id));
    } catch (err) {
      setError(err.message);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    apiGet(`/api/analysis-runs/visual-review/queue?review_status=${reviewStatus}&limit=500`)
      .then((data) => {
        if (!active) return;
        setQueue(data);
        const remembered = Number(window.localStorage.getItem("visual-review-last-unit-id") || 0);
        const rememberedIndex = (data.items ?? []).findIndex((item) => item.id === remembered);
        setCurrentIndex(rememberedIndex >= 0 ? rememberedIndex : 0);
      })
      .catch((err) => { if (active) setError(err.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reviewStatus]);

  useEffect(() => {
    if (current) loadDetail(current);
    else setDetail(null);
  }, [current?.id]);

  useEffect(() => {
    if (!formKey) return;
    window.localStorage.setItem(`visual-review-draft-${formKey}`, JSON.stringify(form));
  }, [form, formKey]);

  function chooseDecision(decision) {
    if (
      decision === "confirmed"
      && detail?.review_kind === "ocr_rescue"
      && !detail?.review_binding?.ocr_candidate_text_sha256
    ) return;
    setForm((value) => ({
      ...value,
      decision,
      reviewedText: decision === "corrected" ? value.reviewedText : (detail?.review_text || ""),
      reason: value.reason || defaultVisualReason(decision),
    }));
    setMessage("");
  }

  function regionPoint(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  }

  function beginRegionMark(event) {
    if (!regionMarking || event.button !== 0) return;
    const point = regionPoint(event);
    if (!point) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setRegionStart({ ...point, pointerId: event.pointerId });
  }

  function finishRegionMark(event) {
    if (!regionMarking || !regionStart || regionStart.pointerId !== event.pointerId) return;
    const point = regionPoint(event);
    setRegionStart(null);
    setRegionMarking(false);
    if (!point) return;
    const x = Math.min(regionStart.x, point.x);
    const y = Math.min(regionStart.y, point.y);
    const width = Math.abs(regionStart.x - point.x);
    const height = Math.abs(regionStart.y - point.y);
    if (width < 0.01 || height < 0.01) {
      setError("Kotak region terlalu kecil. Tarik kotak yang lebih jelas pada gambar.");
      return;
    }
    const regionType = form.regionType || "other";
    setForm((value) => ({
      ...value,
      semanticRegions: [
        ...(value.semanticRegions || []),
        {
          region_type: regionType,
          semantic_hint: regionType,
          label: value.regionLabel.trim(),
          bbox: { x, y, width, height },
          coordinate_space: "normalized_top_left",
          detection_method: "human_visual_region_v1",
          requires_human_confirmation: false,
        },
      ].slice(0, 50),
      regionLabel: "",
    }));
    setError("");
  }

  function removeReviewedRegion(index) {
    setForm((value) => ({
      ...value,
      semanticRegions: (value.semanticRegions || []).filter((_, itemIndex) => itemIndex !== index),
    }));
  }

  async function saveDecision() {
    if (!current || !detail) return;
    if (reviewerId.trim().length < 2) {
      setError("Isi nama atau identitas reviewer.");
      return;
    }
    if (!form.decision) {
      setError("Pilih hasil pemeriksaan visual.");
      return;
    }
    if (form.reason.trim().length < 8) {
      setError("Tuliskan alasan minimal 8 karakter.");
      return;
    }
    if (["confirmed", "corrected"].includes(form.decision) && form.semanticDescription.trim().length < 8) {
      setError("Tuliskan ringkasan makna visual minimal 8 karakter.");
      return;
    }
    if (form.decision === "corrected" && form.reviewedText.trim().length < 8) {
      setError("Tuliskan koreksi teks/deskripsi minimal 8 karakter.");
      return;
    }
    if (!form.attested) {
      setError("Centang pernyataan bahwa gambar sumber sudah diperiksa.");
      return;
    }
    setAction("save");
    setError("");
    setMessage("");
    try {
      await apiPost(
        `/api/analysis-runs/visual-review/${current.run_id}/${encodeURIComponent(current.unit_key)}/decision`,
        {
          reviewer_id: reviewerId.trim(),
          review_kind: detail.review_kind,
          decision: form.decision,
          unit_text_sha256: detail.review_binding.unit_text_sha256,
          source_image_sha256: detail.review_binding.source_image_sha256,
          ocr_candidate_text_sha256: detail.review_binding.ocr_candidate_text_sha256,
          reviewed_text: form.decision === "corrected" ? form.reviewedText.trim() : "",
          semantic_description: form.semanticDescription.trim(),
          semantic_regions: ["confirmed", "corrected"].includes(form.decision)
            ? (form.semanticRegions || []).map((region) => ({
                region_type: region.region_type,
                label: region.label || "",
                bbox: region.bbox,
              }))
            : [],
          reason: form.reason.trim(),
          expected_latest_decision_id: detail.latest_decision?.id || null,
          attested: true,
        },
      );
      window.localStorage.setItem("visual-reviewer-id", reviewerId.trim());
      window.localStorage.removeItem(`visual-review-draft-${formKey}`);
      setMessage("Keputusan review tersimpan append-only. Run lama belum berubah.");
      const refreshed = await loadQueue({ afterSave: true });
      const refreshedItems = refreshed.items ?? [];
      const nextItem = refreshedItems.find((item) => item.review_state !== "reviewed")
        || refreshedItems.find((item) => item.id === current.id)
        || refreshedItems[0];
      if (nextItem) await loadDetail(nextItem);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  async function applyRunReviews() {
    const snapshot = detail?.visual_review_snapshot;
    if (!current || !snapshot?.checksum) return;
    if (reviewerId.trim().length < 2 || applyReason.trim().length < 8) {
      setError("Isi reviewer dan alasan penerapan minimal 8 karakter.");
      return;
    }
    if (!applyAttested) {
      setError("Centang pernyataan penerapan run turunan.");
      return;
    }
    setAction("apply");
    setError("");
    setMessage("");
    try {
      const result = await apiPost(`/api/analysis-runs/visual-review/${current.run_id}/apply`, {
        reviewer_id: reviewerId.trim(),
        visual_review_checksum: snapshot.checksum,
        reason: applyReason.trim(),
        attested: true,
      });
      setMessage(`Run turunan masuk antrean dengan Job ${result.job.id}. Run sumber tetap utuh untuk audit.`);
      setApplyAttested(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction("");
    }
  }

  const runSummary = detail?.run_review_summary || {};
  const manualTranscriptionRequired = Boolean(
    detail?.review_kind === "ocr_rescue"
    && !detail?.review_binding?.ocr_candidate_text_sha256
  );
  const runUnresolved = Number(runSummary.pending || 0) + Number(runSummary.needs_attention || 0);
  const canApply = Boolean(
    detail?.visual_review_snapshot?.checksum
    && detail?.visual_review_snapshot?.actionable_count > 0
    && runUnresolved === 0
  );

  return (
    <section className="guided-review-page visual-review-page">
      <button className="back-button" type="button" onClick={onBack}><ArrowLeft size={18} /> Dashboard</button>
      <div className="guided-review-header">
        <div>
          <p className="eyebrow">Verifikasi gambar dan rescue OCR tanpa kode</p>
          <h2>Review Visual</h2>
          <p>Periksa gambar asli dan teks OCR, termasuk kandidat di bawah ambang. Keputusan disimpan append-only lalu diterapkan melalui run turunan.</p>
        </div>
        <div className="guided-progress-card">
          <strong>{queue.counts?.reviewed ?? 0} / {queue.total ?? 0}</strong>
          <span>unit review selesai</span>
          <div><i style={{ width: `${queue.total ? ((queue.counts?.reviewed ?? 0) / queue.total) * 100 : 0}%` }} /></div>
        </div>
      </div>

      <div className="guided-summary-grid">
        <button className={reviewStatus === "all" ? "active" : ""} type="button" onClick={() => setReviewStatus("all")}><strong>{queue.total ?? 0}</strong><span>Semua</span></button>
        <button className={reviewStatus === "pending" ? "active" : ""} type="button" onClick={() => setReviewStatus("pending")}><strong>{queue.counts?.pending ?? 0}</strong><span>Belum direview</span></button>
        <button className={reviewStatus === "needs_attention" ? "active" : ""} type="button" onClick={() => setReviewStatus("needs_attention")}><strong>{queue.counts?.needs_attention ?? 0}</strong><span>Belum yakin</span></button>
        <button className={reviewStatus === "reviewed" ? "active" : ""} type="button" onClick={() => setReviewStatus("reviewed")}><strong>{queue.counts?.reviewed ?? 0}</strong><span>Sudah direview</span></button>
      </div>

      <p className="visual-review-kind-summary">
        {queue.kind_counts?.visual_semantics ?? 0} verifikasi makna visual · {queue.kind_counts?.ocr_rescue ?? 0} OCR rescue manual
      </p>

      {error ? <Notice tone="danger" text={error} /> : null}
      {message ? <Notice tone="info" text={message} /> : null}
      {loading ? (
        <section className="loading-state"><Loader2 className="spin" size={28} /><span>Menyiapkan antrean visual...</span></section>
      ) : !current ? (
        <EmptyState text="Tidak ada unit review pada filter ini. Unit akan muncul saat makna visual perlu diverifikasi atau kandidat OCR berkepercayaan rendah perlu dikoreksi." />
      ) : (
        <div className="guided-review-workspace">
          <div className="guided-document-nav">
            <button type="button" disabled={currentIndex === 0} onClick={() => setCurrentIndex((value) => Math.max(0, value - 1))}><ChevronLeft size={17} /> Sebelumnya</button>
            <label>Visual {currentIndex + 1} dari {items.length}
              <select value={current.id} onChange={(event) => setCurrentIndex(items.findIndex((item) => item.id === Number(event.target.value)))}>
                {items.map((item, index) => <option value={item.id} key={item.id}>{index + 1}. {item.file_name} · {visualSourceLabel(item)}</option>)}
              </select>
            </label>
            <button type="button" disabled={currentIndex >= items.length - 1} onClick={() => setCurrentIndex((value) => Math.min(items.length - 1, value + 1))}>Berikutnya <ChevronRight size={17} /></button>
          </div>

          {detailLoading || !detail ? (
            <section className="loading-state"><Loader2 className="spin" size={26} /><span>Memuat gambar dan checksum...</span></section>
          ) : (
            <>
              <section className="guided-document-card">
                <div><span className={`guided-state ${current.review_state}`}>{current.review_state.replaceAll("_", " ")}</span><h3>{current.file_name}</h3><p>Run #{current.run_id} · {visualSourceLabel(detail.unit)} · {current.review_kind === "ocr_rescue" ? "OCR rescue" : "Makna visual"} · OCR {Math.round(((current.metadata?.ocr_confidence ?? current.metadata?.ocr_review_candidate_confidence) || 0) * 100)}%</p><small>Unit audit: {current.unit_key}</small></div>
                <a className="secondary-button link-button" href={detail.preview_url} target="_blank" rel="noreferrer"><Eye size={17} /> Buka Gambar Penuh</a>
              </section>

              <section className="visual-review-source-grid">
                <article className="visual-preview-card">
                  <div><strong>Gambar sumber</strong><span>Checksum diverifikasi server</span></div>
                  <div className="visual-preview-scroll">
                    <div
                      className={`visual-preview-stage${regionMarking ? " marking" : ""}`}
                      onPointerDown={beginRegionMark}
                      onPointerUp={finishRegionMark}
                      onPointerCancel={() => { setRegionStart(null); setRegionMarking(false); }}
                    >
                      <img draggable="false" src={detail.preview_url} alt={`Preview ${current.file_name} ${current.unit_key}`} />
                      {semanticRegions.map((region, index) => (
                        <span
                          className="visual-semantic-region"
                          style={region.overlayStyle}
                          title={`${region.semantic_hint || region.region_type}${region.label ? ` · ${region.label}` : ""}`}
                          aria-hidden="true"
                          key={region.overlayKey}
                        >{index + 1}</span>
                      ))}
                    </div>
                  </div>
                  {semanticRegions.length ? (
                    <ol className="visual-semantic-region-list" aria-label="Region visual terdeteksi">
                      {semanticRegions.map((region, index) => (
                        <li key={region.overlayKey}><strong>{index + 1}. {region.semantic_hint || region.region_type}</strong><span>{region.label || "Region OOXML terstruktur"}</span></li>
                      ))}
                    </ol>
                  ) : null}
                </article>
                <article className="visual-ocr-card">
                  <div><strong>{detail.review_kind === "ocr_rescue" ? (manualTranscriptionRequired ? "OCR tidak menghasilkan teks" : "Kandidat OCR di bawah ambang") : "Teks OCR saat ini"}</strong><span>{detail.unit.metadata?.ocr_method || detail.unit.metadata?.ocr_review_candidate_method || "local OCR"}</span></div>
                  <pre>{detail.review_text || "Tidak ada teks OCR."}</pre>
                  <small>SHA teks {detail.review_binding?.ocr_candidate_text_sha256?.slice(0, 16) || detail.review_binding?.unit_text_sha256?.slice(0, 16)}… · SHA gambar {detail.review_binding?.source_image_sha256?.slice(0, 16)}…</small>
                </article>
              </section>

              <section className="guided-step-card">
                <div className="guided-step-heading"><span>1</span><div><h3>Pilih hasil pemeriksaan</h3><p>{detail.review_kind === "ocr_rescue" ? (manualTranscriptionRequired ? "Mesin tidak menghasilkan teks. Pilih Perlu koreksi untuk menyalin teks dari gambar, Bukan evidence, atau Belum yakin." : "Kandidat ini ditolak mesin karena confidence rendah; konfirmasi hanya setelah menyalin dan mencocokkan gambar.") : "Jangan mengonfirmasi hanya karena teks terlihat masuk akal; cocokkan dengan gambar."}</p></div></div>
                <div className="guided-outcome-grid">
                  {VISUAL_REVIEW_OUTCOMES.map((item) => (
                    <button className={form.decision === item.value ? `selected ${item.value}` : item.value} type="button" key={item.value} disabled={manualTranscriptionRequired && item.value === "confirmed"} onClick={() => chooseDecision(item.value)}><strong>{item.label}</strong><span>{manualTranscriptionRequired && item.value === "confirmed" ? "Tidak tersedia karena belum ada teks OCR untuk dikonfirmasi." : item.description}</span></button>
                  ))}
                </div>
                {form.decision === "corrected" ? (
                  <label>Teks/deskripsi visual yang benar<textarea value={form.reviewedText} onChange={(event) => setForm((value) => ({ ...value, reviewedText: event.target.value }))} placeholder="Tuliskan hanya hal yang benar-benar terlihat pada gambar" /></label>
                ) : null}
                {["confirmed", "corrected"].includes(form.decision) ? (
                  <label>Ringkasan makna visual<textarea value={form.semanticDescription} onChange={(event) => setForm((value) => ({ ...value, semanticDescription: event.target.value }))} placeholder="Contoh: Gambar menunjukkan judul identifikasi risiko pada lembar kerja" /></label>
                ) : null}
                {["confirmed", "corrected"].includes(form.decision) ? (
                  <div className="visual-region-editor">
                    <div className="visual-region-editor-heading">
                      <div><strong>Region visual (opsional)</strong><span>Tandai lokasi diagram, chart, tanda tangan, atau stempel agar sumber dapat ditelusuri sampai kotaknya.</span></div>
                      <small>Maksimal 50 region</small>
                    </div>
                    <div className="visual-region-controls">
                      <label>Jenis region<select value={form.regionType} onChange={(event) => setForm((value) => ({ ...value, regionType: event.target.value }))}>{VISUAL_REGION_TYPES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
                      <label>Label singkat<input value={form.regionLabel} maxLength="300" onChange={(event) => setForm((value) => ({ ...value, regionLabel: event.target.value }))} placeholder="Contoh: Stempel pengesahan" /></label>
                      <button className={regionMarking ? "primary-button active" : "secondary-button"} type="button" disabled={(form.semanticRegions || []).length >= 50} onClick={() => { setRegionMarking((value) => !value); setRegionStart(null); }}>{regionMarking ? "Tarik kotak pada gambar…" : "Tandai region pada gambar"}</button>
                    </div>
                    {regionMarking ? <Notice tone="info" text="Tarik dari satu sudut ke sudut lain pada gambar sumber. Mode penandaan berhenti otomatis setelah satu kotak dibuat." /> : null}
                    {(form.semanticRegions || []).length ? (
                      <div className="visual-reviewed-region-list">
                        {form.semanticRegions.map((region, index) => (
                          <article key={`${region.region_type}-${index}`}>
                            <div><strong>{VISUAL_REGION_TYPES.find((item) => item.value === region.region_type)?.label || region.region_type}</strong><span>{region.label || "Tanpa label"}</span></div>
                            <small>x {Math.round(region.bbox.x * 100)}% · y {Math.round(region.bbox.y * 100)}% · w {Math.round(region.bbox.width * 100)}% · h {Math.round(region.bbox.height * 100)}%</small>
                            <button type="button" onClick={() => removeReviewedRegion(index)}>Hapus</button>
                          </article>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </section>

              <section className="guided-step-card">
                <div className="guided-step-heading"><span>2</span><div><h3>Catat reviewer dan simpan</h3><p>Keputusan lama tidak ditimpa; revisi menjadi event baru.</p></div></div>
                <div className="guided-reviewer-grid">
                  <label>Nama/identitas reviewer<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Nama atau email dinas" /></label>
                  <label>Alasan pemeriksaan<textarea value={form.reason} onChange={(event) => setForm((value) => ({ ...value, reason: event.target.value }))} placeholder="Jelaskan apa yang diperiksa" /></label>
                </div>
                <label className="governance-attestation"><input type="checkbox" checked={form.attested} onChange={(event) => setForm((value) => ({ ...value, attested: event.target.checked }))} /><span>Saya sudah melihat gambar sumber, membandingkan teks OCR, dan memahami bahwa keputusan ini dapat menghasilkan fakta pada run turunan.</span></label>
                <div className="guided-save-row"><small>Run #{current.run_id} tetap tidak berubah setelah penyimpanan keputusan.</small><button className="primary-button" type="button" disabled={Boolean(action)} onClick={saveDecision}>{action === "save" ? <Loader2 className="spin" size={17} /> : <CheckCircle2 size={17} />} Simpan & Lanjut</button></div>
              </section>

              <section className="guided-step-card visual-apply-card">
                <div className="guided-step-heading"><span>3</span><div><h3>Terapkan pada run turunan</h3><p>{runSummary.reviewed || 0}/{runSummary.total || 0} unit run ini direview; {runUnresolved} masih tertahan.</p></div></div>
                <label>Alasan penerapan<textarea value={applyReason} onChange={(event) => setApplyReason(event.target.value)} /></label>
                <label className="governance-attestation"><input type="checkbox" checked={applyAttested} onChange={(event) => setApplyAttested(event.target.checked)} /><span>Saya memahami aplikasi akan membuat run baru, menghitung ulang fakta/mapping/verifikasi, dan mempertahankan run lama untuk audit.</span></label>
                <div className="guided-save-row"><small>{canApply ? `Checksum ${detail.visual_review_snapshot.checksum.slice(0, 16)}… siap diterapkan.` : "Selesaikan seluruh unit review pada run ini sebelum menerapkan."}</small><button className="primary-button" type="button" disabled={!canApply || Boolean(action)} onClick={applyRunReviews}>{action === "apply" ? <Loader2 className="spin" size={17} /> : <RefreshCw size={17} />} Buat Run Turunan</button></div>
              </section>

              {detail.decision_history?.length ? (
                <section className="guided-step-card"><div className="guided-step-heading"><span>✓</span><div><h3>Riwayat keputusan unit</h3><p>History append-only untuk pemeriksaan ulang.</p></div></div><div className="visual-review-history">{[...detail.decision_history].reverse().slice(0, 10).map((item) => <article key={item.id}><strong>{item.decision}</strong><span>{item.reviewer_id} · {item.created_at}</span><p>{item.reason}</p></article>)}</div></section>
              ) : null}
            </>
          )}
        </div>
      )}
    </section>
  );
}
