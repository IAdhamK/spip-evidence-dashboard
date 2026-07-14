import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FileArchive,
  FileText,
  Loader2,
} from "lucide-react";
import { apiGet, apiPost } from "../lib/api.js";
import { formatSourceLocation } from "../lib/source-location.js";
import { EmptyState, Notice } from "./shared/Feedback.jsx";

const GUIDED_REVIEW_OUTCOMES = [
  {
    value: "confirmed",
    label: "Saran sudah benar",
    description: "Parameter dan sumber sesuai setelah diperiksa.",
  },
  {
    value: "corrected",
    label: "Pilih parameter lain",
    description: "Dokumen relevan, tetapi saran sistem perlu dikoreksi.",
  },
  {
    value: "not_evidence",
    label: "Bukan evidence",
    description: "Dokumen tidak dapat dipakai sebagai evidence parameter SPIP.",
  },
  {
    value: "unsure",
    label: "Belum yakin",
    description: "Simpan sementara untuk diperiksa domain owner.",
  },
];

export default function GuidedReviewPage({ onBack }) {
  const [queue, setQueue] = useState({ total: 0, counts: {}, items: [] });
  const [reviewStatus, setReviewStatus] = useState("all");
  const [currentIndex, setCurrentIndex] = useState(0);
  const [detail, setDetail] = useState(null);
  const [parameters, setParameters] = useState([]);
  const [parameterQuery, setParameterQuery] = useState("");
  const [reviewerId, setReviewerId] = useState(() => window.localStorage.getItem("guided-reviewer-id") || "");
  const [form, setForm] = useState({
    outcome: "",
    mappingId: "",
    factIds: [],
    parameterKey: "",
    grade: "",
    evidenceRole: "",
    templateStatus: "",
    reason: "",
  });
  const [formRunId, setFormRunId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const items = queue.items ?? [];
  const current = items[currentIndex] ?? null;
  const selectedMapping = (detail?.mappings ?? []).find((item) => item.id === Number(form.mappingId));
  const supportingFactIds = new Set((selectedMapping?.supporting_fact_ids ?? []).map(Number));
  const sourceRequired = ["confirmed", "corrected"].includes(form.outcome);
  const templateRequired = Boolean(form.outcome && form.outcome !== "unsure");
  const completionChecks = [
    {
      key: "outcome",
      done: Boolean(form.outcome),
      label: "Hasil pemeriksaan sudah dipilih",
    },
    {
      key: "mapping",
      done: form.outcome === "confirmed"
        ? Boolean(form.mappingId)
        : form.outcome === "corrected"
          ? Boolean(form.parameterKey)
          : Boolean(form.outcome),
      label: form.outcome === "corrected"
        ? "Parameter pengganti sudah dipilih"
        : "Saran parameter sudah diperiksa",
    },
    {
      key: "source",
      done: !sourceRequired || form.factIds.length > 0,
      label: sourceRequired
        ? "Minimal satu fakta sumber sudah dicentang"
        : "Lokasi sumber tidak diwajibkan untuk pilihan ini",
    },
    {
      key: "evidence-role",
      done: !sourceRequired || Boolean(form.evidenceRole),
      label: sourceRequired
        ? "Peran evidence sudah diperiksa"
        : "Peran evidence tidak diwajibkan untuk pilihan ini",
    },
    {
      key: "template-status",
      done: !templateRequired || Boolean(form.templateStatus),
      label: templateRequired
        ? "Status template dokumen sudah diperiksa"
        : "Status template belum diwajibkan untuk pilihan ini",
    },
    {
      key: "reviewer",
      done: reviewerId.trim().length >= 2,
      label: "Nama reviewer sudah diisi",
    },
    {
      key: "reason",
      done: form.reason.trim().length >= 8,
      label: "Alasan singkat sudah cukup",
    },
  ];
  const formReady = completionChecks.every((item) => item.done);
  const visibleParameters = useMemo(() => {
    const needle = parameterQuery.trim().toLowerCase();
    if (!needle) return parameters;
    const tokens = needle.split(/\s+/).filter(Boolean);
    return parameters.filter((parameter) => {
      const haystack = [
        parameter.kk_id,
        parameter.detail_kode,
        parameter.uraian,
        parameter.subunsur_name,
      ].map((value) => String(value || "").toLowerCase()).join(" ");
      return tokens.every((token) => haystack.includes(token));
    });
  }, [parameters, parameterQuery]);

  function parameterValue(item) {
    return `${item.kk_id}|${item.kode}|${item.detail_kode}`;
  }

  function outcomeDefaultReason(outcome) {
    return {
      confirmed: "Parameter dan lokasi sumber sesuai setelah diperiksa.",
      corrected: "Dokumen relevan, tetapi parameter perlu dikoreksi.",
      not_evidence: "Dokumen tidak memenuhi syarat sebagai evidence parameter.",
      unsure: "Perlu pemeriksaan domain owner sebelum dipastikan.",
    }[outcome] || "";
  }

  async function loadQueue({ afterSave = false } = {}) {
    const data = await apiGet(`/api/analysis-runs/guided-review/queue?review_status=${reviewStatus}&limit=500`);
    setQueue(data);
    const nextItems = data.items ?? [];
    if (!nextItems.length) {
      setCurrentIndex(0);
      setDetail(null);
      return data;
    }
    if (afterSave) {
      const firstPending = nextItems.findIndex((item) => item.review_state !== "completed");
      setCurrentIndex(firstPending >= 0 ? firstPending : Math.min(currentIndex, nextItems.length - 1));
      return data;
    }
    const remembered = Number(window.localStorage.getItem("guided-review-last-run-id") || 0);
    const rememberedIndex = nextItems.findIndex((item) => item.id === remembered);
    setCurrentIndex(rememberedIndex >= 0 ? rememberedIndex : Math.min(currentIndex, nextItems.length - 1));
    return data;
  }

  async function loadDetail(runId) {
    setDetailLoading(true);
    setError("");
    try {
      const data = await apiGet(`/api/analysis-runs/guided-review/${runId}`);
      const active = data.active_label;
      let initial = {
        outcome: active?.outcome || "",
        mappingId: active?.selected_mapping_candidate_id || data.mappings?.[0]?.id || "",
        factIds: active?.selected_fact_ids || [],
        parameterKey: active?.expected_mappings?.[0]
          ? parameterValue(active.expected_mappings[0])
          : "",
        grade: active?.expected_mappings?.[0]?.grade || "",
        evidenceRole: active?.expected_mappings?.[0]?.evidence_role || "",
        templateStatus: active?.expected_template_status === "not_assessed"
          ? ""
          : active?.expected_template_status || "",
        reason: active?.reason || "",
      };
      try {
        const draft = JSON.parse(window.localStorage.getItem(`guided-review-draft-${runId}`) || "null");
        if (draft && typeof draft === "object") initial = { ...initial, ...draft };
      } catch {
        // Draft yang rusak diabaikan; label tersimpan tetap menjadi sumber utama.
      }
      setDetail(data);
      setForm(initial);
      setFormRunId(runId);
      if (active?.reviewer_id && !reviewerId) setReviewerId(active.reviewer_id);
      window.localStorage.setItem("guided-review-last-run-id", String(runId));
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
    Promise.all([
      apiGet("/api/analysis-runs/guided-review/parameters?limit=500"),
      apiGet(`/api/analysis-runs/guided-review/queue?review_status=${reviewStatus}&limit=500`),
    ]).then(([parameterData, queueData]) => {
      if (!active) return;
      setParameters(parameterData.parameters ?? []);
      setQueue(queueData);
      const remembered = Number(window.localStorage.getItem("guided-review-last-run-id") || 0);
      const rememberedIndex = (queueData.items ?? []).findIndex((item) => item.id === remembered);
      setCurrentIndex(rememberedIndex >= 0 ? rememberedIndex : 0);
    }).catch((err) => {
      if (active) setError(err.message);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [reviewStatus]);

  useEffect(() => {
    if (current?.id) loadDetail(current.id);
    else setDetail(null);
  }, [current?.id]);

  useEffect(() => {
    if (!formRunId || detail?.run?.id !== formRunId) return;
    window.localStorage.setItem(`guided-review-draft-${formRunId}`, JSON.stringify(form));
  }, [form, formRunId, detail?.run?.id]);

  function chooseOutcome(outcome) {
    setForm((currentForm) => ({
      ...currentForm,
      outcome,
      factIds: ["not_evidence", "unsure"].includes(outcome) ? [] : currentForm.factIds,
      reason: currentForm.reason || outcomeDefaultReason(outcome),
    }));
    setMessage("");
  }

  function chooseMapping(mappingId) {
    const mapping = (detail?.mappings ?? []).find((item) => item.id === Number(mappingId));
    const allowed = new Set((mapping?.supporting_fact_ids ?? []).map(Number));
    setForm((currentForm) => ({
      ...currentForm,
      mappingId,
      factIds: currentForm.outcome === "confirmed"
        ? currentForm.factIds.filter((factId) => allowed.has(Number(factId)))
        : currentForm.factIds,
    }));
  }

  function toggleFact(factId) {
    setForm((currentForm) => ({
      ...currentForm,
      factIds: currentForm.factIds.includes(factId)
        ? currentForm.factIds.filter((item) => item !== factId)
        : [...currentForm.factIds, factId],
    }));
  }

  async function saveAndContinue() {
    if (!current || !detail) return;
    if (reviewerId.trim().length < 2) {
      setError("Isi nama atau identitas reviewer.");
      return;
    }
    if (!form.outcome) {
      setError("Pilih hasil review terlebih dahulu.");
      return;
    }
    if (form.reason.trim().length < 8) {
      setError("Tuliskan alasan singkat minimal 8 karakter.");
      return;
    }
    if (form.outcome === "confirmed" && !form.mappingId) {
      setError("Pilih saran parameter yang dinyatakan benar.");
      return;
    }
    if (form.outcome === "corrected" && !form.parameterKey) {
      setError("Pilih parameter pengganti dari daftar resmi.");
      return;
    }
    if (sourceRequired && !form.factIds.length) {
      setError("Centang minimal satu fakta dan lokasi sumber.");
      return;
    }
    if (sourceRequired && !form.evidenceRole) {
      setError("Pilih peran evidence setelah memeriksa sumber.");
      return;
    }
    if (templateRequired && !form.templateStatus) {
      setError("Pilih apakah dokumen merupakan template kosong atau memiliki isi substantif.");
      return;
    }
    const [kkId, kode, detailKode] = form.parameterKey.split("|");
    setSaving(true);
    setError("");
    setMessage("");
    try {
      await apiPost(`/api/analysis-runs/guided-review/${current.id}`, {
        reviewer_id: reviewerId.trim(),
        outcome: form.outcome,
        selected_mapping_candidate_id: form.mappingId ? Number(form.mappingId) : null,
        selected_source_fact_ids: form.factIds.map(Number),
        expected_mapping: form.outcome === "corrected"
          ? { kk_id: kkId, kode, detail_kode: detailKode, grade: form.grade || null }
          : {},
        expected_evidence_role: sourceRequired ? form.evidenceRole : null,
        expected_template_status: templateRequired ? form.templateStatus : "not_assessed",
        reason: form.reason.trim(),
      });
      window.localStorage.setItem("guided-reviewer-id", reviewerId.trim());
      window.localStorage.removeItem(`guided-review-draft-${current.id}`);
      setMessage("Review tersimpan sebagai kandidat label ahli. Upload utama tetap terkunci.");
      await loadQueue({ afterSave: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  const orderedFacts = [...(detail?.facts ?? [])].sort((left, right) => {
    const leftSupported = supportingFactIds.has(Number(left.id)) ? 0 : 1;
    const rightSupported = supportingFactIds.has(Number(right.id)) ? 0 : 1;
    return leftSupported - rightSupported || Number(left.id) - Number(right.id);
  });

  return (
    <section className="guided-review-page">
      <button className="back-button" type="button" onClick={onBack}>
        <ArrowLeft size={18} /> Dashboard
      </button>

      <div className="guided-review-header">
        <div>
          <p className="eyebrow">Review tanpa kode teknis</p>
          <h2>Wizard Review Terpandu</h2>
          <p>Periksa saran, pilih sumber, lalu simpan. Hasil menjadi kandidat dataset ahli dan tidak membuka upload produksi.</p>
        </div>
        <div className="guided-header-actions">
          <div className="guided-progress-card">
            <strong>{queue.counts?.completed ?? 0} / {queue.total ?? 0}</strong>
            <span>review selesai</span>
            <div><i style={{ width: `${queue.total ? ((queue.counts?.completed ?? 0) / queue.total) * 100 : 0}%` }} /></div>
          </div>
          <a className="secondary-button link-button" href="/api/analysis-runs/guided-review/export">
            <FileArchive size={17} /> Unduh Hasil Review
          </a>
        </div>
      </div>

      <section className="guided-easy-path" aria-label="Cara mudah menyiapkan dataset ahli">
        <article className="active"><span>1</span><div><strong>Anda periksa 50 dokumen</strong><small>Pilih jawaban dan sumber pada wizard ini.</small></div></article>
        <article><span>2</span><div><strong>Orang kedua mengesahkan</strong><small>Domain owner membuka Governance V2 → Dataset Ahli.</small></div></article>
        <article><span>3</span><div><strong>Aplikasi mengukur hasil</strong><small>Recall, akurasi sumber, dan overgrade dihitung otomatis.</small></div></article>
      </section>

      <div className="guided-summary-grid">
        <button className={reviewStatus === "all" ? "active" : ""} type="button" onClick={() => setReviewStatus("all")}><strong>{queue.total ?? 0}</strong><span>Semua</span></button>
        <button className={reviewStatus === "pending" ? "active" : ""} type="button" onClick={() => setReviewStatus("pending")}><strong>{queue.counts?.pending ?? 0}</strong><span>Belum direview</span></button>
        <button className={reviewStatus === "needs_attention" ? "active" : ""} type="button" onClick={() => setReviewStatus("needs_attention")}><strong>{queue.counts?.needs_attention ?? 0}</strong><span>Belum yakin</span></button>
        <button className={reviewStatus === "completed" ? "active" : ""} type="button" onClick={() => setReviewStatus("completed")}><strong>{queue.counts?.completed ?? 0}</strong><span>Selesai</span></button>
      </div>

      {error ? <Notice tone="danger" text={error} /> : null}
      {message ? <Notice tone="info" text={message} /> : null}

      {loading ? (
        <section className="loading-state"><Loader2 className="spin" size={28} /><span>Menyiapkan antrean review...</span></section>
      ) : !current ? (
        <EmptyState text="Tidak ada dokumen pada filter ini. Unggah dan jalankan Full Audit V2 untuk menambah antrean." />
      ) : (
        <div className="guided-review-workspace">
          <div className="guided-document-nav">
            <button type="button" disabled={currentIndex === 0} onClick={() => setCurrentIndex((value) => Math.max(0, value - 1))}><ChevronLeft size={17} /> Sebelumnya</button>
            <label>
              Dokumen {currentIndex + 1} dari {items.length}
              <select value={current.id} onChange={(event) => setCurrentIndex(items.findIndex((item) => item.id === Number(event.target.value)))}>
                {items.map((item, index) => <option value={item.id} key={item.id}>{index + 1}. {item.file_name}</option>)}
              </select>
            </label>
            <button type="button" disabled={currentIndex >= items.length - 1} onClick={() => setCurrentIndex((value) => Math.min(items.length - 1, value + 1))}>Berikutnya <ChevronRight size={17} /></button>
          </div>

          {detailLoading || !detail ? (
            <section className="loading-state"><Loader2 className="spin" size={26} /><span>Memuat fakta dan saran...</span></section>
          ) : (
            <>
              <section className="guided-document-card">
                <div>
                  <span className={`guided-state ${current.review_state}`}>{current.review_state.replaceAll("_", " ")}</span>
                  <h3>{current.file_name}</h3>
                  <p>Coverage {Math.round(current.coverage_percentage ?? 0)}% · {current.fact_count} fakta · {current.mapping_count} saran parameter</p>
                </div>
                <a className="secondary-button link-button" href={`/api/analysis-runs/guided-review/${current.id}/document`} target="_blank" rel="noreferrer">
                  <FileText size={17} /> Buka Dokumen
                </a>
              </section>

              {current.block_reasons?.length ? <div className="gate-warning-list">{current.block_reasons.map((reason) => <span key={reason}>{reason}</span>)}</div> : null}

              <section className="guided-step-card">
                <div className="guided-step-heading"><span>1</span><div><h3>Periksa saran parameter</h3><p>Pilih saran yang paling sesuai. Anda tidak perlu mengetik kode.</p></div></div>
                <div className="guided-mapping-list">
                  {(detail.mappings ?? []).map((mapping, index) => (
                    <label className={Number(form.mappingId) === mapping.id ? "guided-mapping-option selected" : "guided-mapping-option"} key={mapping.id}>
                      <input type="radio" name="guided-mapping" checked={Number(form.mappingId) === mapping.id} onChange={() => chooseMapping(mapping.id)} />
                      <span className="guided-mapping-rank">#{index + 1}</span>
                      <span>
                        <strong>{mapping.parameter_uraian || `${mapping.kk_id} / ${mapping.detail_kode}`}</strong>
                        <small>{mapping.subunsur_name} · Skor kecocokan {Math.round((mapping.mapping_score ?? 0) * 100)}% · Grade usulan {mapping.candidate_grade || "-"}</small>
                        {(mapping.reasons ?? []).slice(0, 2).map((reason) => <em key={reason}>{reason}</em>)}
                      </span>
                    </label>
                  ))}
                  {!detail.mappings?.length ? <EmptyState text="Sistem abstain dan tidak memaksakan parameter. Pilih Bukan evidence atau Belum yakin." /> : null}
                </div>
              </section>

              <section className="guided-step-card">
                <div className="guided-step-heading"><span>2</span><div><h3>Pilih hasil pemeriksaan</h3><p>Empat pilihan ini cukup; istilah teknis disimpan otomatis di belakang layar.</p></div></div>
                <div className="guided-outcome-grid">
                  {GUIDED_REVIEW_OUTCOMES.map((item) => (
                    <button className={form.outcome === item.value ? `selected ${item.value}` : item.value} type="button" key={item.value} onClick={() => chooseOutcome(item.value)}>
                      <strong>{item.label}</strong><span>{item.description}</span>
                    </button>
                  ))}
                </div>
                {form.outcome === "corrected" ? (
                  <div className="guided-correction-grid">
                    <label className="guided-parameter-search">Cari parameter
                      <input value={parameterQuery} onChange={(event) => setParameterQuery(event.target.value)} placeholder="Ketik kata, subunsur, atau kode parameter" />
                    </label>
                    <label>Parameter yang benar
                      <select value={form.parameterKey} onChange={(event) => setForm((value) => ({ ...value, parameterKey: event.target.value }))}>
                        <option value="">Pilih dari daftar resmi...</option>
                        {visibleParameters.map((parameter) => <option value={parameterValue(parameter)} key={parameterValue(parameter)}>{parameter.kk_id} · {parameter.detail_kode} · {parameter.uraian}</option>)}
                      </select>
                      <small>{visibleParameters.length} parameter cocok</small>
                    </label>
                    <label>Grade bila sudah yakin
                      <select value={form.grade} onChange={(event) => setForm((value) => ({ ...value, grade: event.target.value }))}>
                        <option value="">Belum ditentukan</option>
                        {["A", "B", "C", "D", "E"].map((grade) => <option value={grade} key={grade}>Grade {grade}</option>)}
                      </select>
                    </label>
                  </div>
                ) : null}
                {sourceRequired ? (
                  <div className="guided-correction-grid">
                    <label>Peran evidence setelah diperiksa
                      <select value={form.evidenceRole} onChange={(event) => setForm((value) => ({ ...value, evidenceRole: event.target.value }))}>
                        <option value="">Pilih peran evidence...</option>
                        <option value="primary">Utama — pelaksanaan, hasil, evaluasi, atau perbaikan</option>
                        <option value="supporting">Pendukung — kebijakan atau sosialisasi</option>
                        <option value="context">Konteks — membantu memahami, bukan bukti utama</option>
                        <option value="contradictory">Kontradiktif — menunjukkan syarat belum/tidak terpenuhi</option>
                      </select>
                      <small>Peran ini dicatat untuk evaluasi dan tidak menentukan grade secara otomatis.</small>
                    </label>
                  </div>
                ) : null}
                {templateRequired ? (
                  <div className="guided-correction-grid">
                    <label>Status isi dokumen
                      <select value={form.templateStatus} onChange={(event) => setForm((value) => ({ ...value, templateStatus: event.target.value }))}>
                        <option value="">Pilih setelah membaca dokumen...</option>
                        <option value="substantive">Memiliki isi substantif/aktivitas nyata</option>
                        <option value="template_only">Hanya template, instruksi, atau kolom kosong</option>
                      </select>
                      <small>Jawaban manusia ini dipakai untuk mengukur akurasi Template Completeness Engine.</small>
                    </label>
                  </div>
                ) : null}
              </section>

              {sourceRequired ? (
                <section className="guided-step-card">
                  <div className="guided-step-heading"><span>3</span><div><h3>Konfirmasi fakta dan lokasi sumber</h3><p>Centang hanya fakta yang sudah Anda lihat pada dokumen.</p></div></div>
                  <div className="guided-fact-list">
                    {orderedFacts.map((fact) => {
                      const eligible = form.outcome !== "confirmed" || supportingFactIds.has(Number(fact.id));
                      return (
                        <label className={!eligible ? "guided-fact-option disabled" : form.factIds.includes(fact.id) ? "guided-fact-option selected" : "guided-fact-option"} key={fact.id}>
                          <input type="checkbox" disabled={!eligible} checked={form.factIds.includes(fact.id)} onChange={() => toggleFact(fact.id)} />
                          <span>
                            <strong>{fact.claim}</strong>
                            <small>Peran sistem: {(fact.evidence_role || "context").replaceAll("_", " ")} · advisory, bukan grade</small>
                            {(fact.sources ?? []).map((source) => <small key={source.id}>{formatSourceLocation(source.source_location, source.unit_key)} {source.source_quote_verified ? "· kutipan cocok" : "· perlu cek"}</small>)}
                          </span>
                        </label>
                      );
                    })}
                    {!orderedFacts.length ? <EmptyState text="Belum ada fakta bersumber; pilih Belum yakin atau Bukan evidence." /> : null}
                  </div>
                </section>
              ) : null}

              <section className="guided-step-card">
                <div className="guided-step-heading"><span>{sourceRequired ? "4" : "3"}</span><div><h3>Catat reviewer dan alasan</h3><p>Nama disimpan di perangkat ini agar tidak perlu diketik berulang.</p></div></div>
                <div className="guided-reviewer-grid">
                  <label>Nama/identitas reviewer<input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Contoh: Nama Anda atau email dinas" /></label>
                  <label>Alasan singkat<textarea value={form.reason} onChange={(event) => setForm((value) => ({ ...value, reason: event.target.value }))} placeholder="Jelaskan apa yang sudah diperiksa" /></label>
                </div>
                <div className={`guided-completion-checklist ${formReady ? "ready" : "pending"}`} aria-live="polite">
                  <div><strong>{formReady ? "Siap disimpan" : "Lengkapi yang belum dicentang"}</strong><span>{completionChecks.filter((item) => item.done).length}/{completionChecks.length} lengkap</span></div>
                  <ul>
                    {completionChecks.map((item) => <li className={item.done ? "done" : ""} key={item.key}><span>{item.done ? "✓" : "○"}</span>{item.label}</li>)}
                  </ul>
                </div>
                <div className="guided-save-row">
                  <small>Draft tersimpan otomatis. Jika ragu, pilih “Belum yakin”; jangan menebak.</small>
                  <button className="primary-button" type="button" disabled={saving || !formReady} onClick={saveAndContinue}>
                    {saving ? <Loader2 className="spin" size={17} /> : <CheckCircle2 size={17} />}
                    Simpan & Lanjut
                  </button>
                </div>
              </section>
            </>
          )}
        </div>
      )}
    </section>
  );
}
