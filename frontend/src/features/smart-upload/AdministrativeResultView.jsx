import { CheckCircle2, Info, Loader2, PencilLine, XCircle } from "lucide-react";
import { EmptyState } from "../shared/Feedback.jsx";
import {
  administrativeReviewGroups,
  administrativeRunStatus,
  confidenceLabel,
} from "./admin-result.js";

export default function AdministrativeResultView({
  run,
  primaryResult,
  mappings,
  reviewIntent,
  reviewerId,
  setReviewerId,
  reviewReason,
  setReviewReason,
  correctionTarget,
  setCorrectionTarget,
  correctionGrade,
  setCorrectionGrade,
  reviewAction,
  reviewMessage,
  beginReview,
  submitReview,
  cancelReview,
}) {
  if (!primaryResult) {
    return (
      <section className="admin-result-view">
        <EmptyState text="Belum ada parameter yang cukup didukung oleh isi dokumen. Periksa kembali dokumen atau tambahkan dokumen pendukung." />
      </section>
    );
  }

  const { mapping, assessment, verifications, direction } = primaryResult;
  const reviewGroups = administrativeReviewGroups({ run, assessment, verifications });
  const hasReviewItems = Object.values(reviewGroups).some((items) => items.length);
  const parameterLabel = mapping.parameter_uraian || mapping.subunsur_name || "Uraian parameter belum tersedia";
  const evidenceGrade = assessment.candidate_grade && assessment.primary_allowed === true
    ? `Grade ${assessment.candidate_grade}`
    : "Belum dapat ditetapkan";
  const documentRoleLabels = {
    primary: "Bukti utama",
    supporting: "Dokumen pendukung",
    context: "Dokumen konteks",
    not_evidence: "Belum teridentifikasi sebagai evidence",
  };
  const documentRole = documentRoleLabels[mapping.document_role] || "Peran dokumen perlu diperiksa";
  const subunsurLabel = mapping.subunsur_name || mapping.matrix_subunsur_name || "Belum ditemukan";
  const decisionLabels = {
    approve: "Hasil Benar",
    correct: "Perbaiki Hasil",
    reject: "Bukan Evidence",
  };

  return (
    <section className="admin-result-view" aria-label="Ringkasan hasil untuk petugas administrasi">
      <div className="admin-result-intro">
        <div>
          <span className="admin-eyebrow">Hasil pencarian otomatis yang paling sesuai</span>
          <h4>{mapping.kk_id} · Parameter {mapping.detail_kode}</h4>
          <p>Sistem telah menelusuri katalog KK, subunsur, parameter, dan aturan Grade. Periksa ringkasan berikut sebelum menyimpan keputusan.</p>
        </div>
        <span className="admin-status-badge">{administrativeRunStatus(run)}</span>
      </div>

      <div className="admin-classification-path" aria-label="Hasil klasifikasi dokumen">
        <div><span>KK yang ditemukan</span><strong>{mapping.kk_id}</strong><small>{mapping.kk_title || "Nama KK belum tersedia"}</small></div>
        <div><span>Unsur</span><strong>{mapping.unsur || mapping.matrix_subunsur_name || "Belum ditemukan"}</strong></div>
        <div><span>Subunsur</span><strong>{mapping.kode}</strong><small>{subunsurLabel}</small></div>
        <div><span>Parameter</span><strong>{mapping.detail_kode}</strong><small>{parameterLabel}</small></div>
      </div>

      <div className={`admin-document-role admin-document-role-${mapping.document_role || "unknown"}`}>
        <strong>Peran dokumen: {documentRole}</strong>
        <span>{mapping.document_role === "primary"
          ? "Isi dokumen dapat diperiksa sebagai bukti pelaksanaan, hasil, evaluasi, atau tindak lanjut."
          : "Dokumen ini membantu menemukan parameter, tetapi Grade memerlukan bukti utama yang dirujuk atau dilampirkan."}</span>
      </div>

      <div className="admin-result-metrics">
        <div>
          <span>Kecocokan</span>
          <strong>{confidenceLabel(mapping.mapping_score)} · {Math.round((mapping.mapping_score ?? 0) * 100)}%</strong>
          <small>Kesesuaian isi dokumen dengan parameter</small>
        </div>
        <div className="grade-direction">
          <span>Arah Grade</span>
          <strong>{direction.label}</strong>
          <small>Perkiraan awal, bukan keputusan akhir</small>
        </div>
        <div>
          <span>Grade yang sudah terbukti</span>
          <strong>{evidenceGrade}</strong>
          <small>Berdasarkan syarat yang berhasil ditemukan</small>
        </div>
      </div>

      <div className="admin-explanation">
        <Info size={18} aria-hidden="true" />
        <p><strong>Arah Grade</strong> dicari setelah KK, subunsur, dan parameter ditemukan. Grade resmi baru ditetapkan bila bukti utama dan persyaratannya lengkap.</p>
      </div>

      {reviewGroups.missing.length ? (
        <div className="admin-missing-panel">
          <div><strong>Belum ditemukan dalam bukti</strong><span>{reviewGroups.missing.length} hal</span></div>
          <ul>{reviewGroups.missing.slice(0, 5).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}

      {reviewGroups.detected.length ? (
        <div className="admin-detected-panel">
          <div><strong>Sudah dikenali oleh sistem</strong><span>{reviewGroups.detected.length} hal</span></div>
          <ul>{reviewGroups.detected.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}

      {reviewGroups.confirmations.length ? (
        <div className="admin-confirmation-panel">
          <div><strong>Perlu konfirmasi pemeriksa</strong><span>{reviewGroups.confirmations.length} hal</span></div>
          <ul>{reviewGroups.confirmations.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}

      {reviewGroups.governance.length ? (
        <div className="admin-governance-panel">
          <div><strong>Status pedoman penilaian</strong></div>
          <ul>{reviewGroups.governance.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}

      {!hasReviewItems ? (
        <div className="admin-ready-panel"><CheckCircle2 size={18} />Informasi utama sudah ditemukan. Silakan periksa dan konfirmasi hasilnya.</div>
      ) : null}

      <div className="admin-decision-actions" aria-label="Keputusan pemeriksaan">
        <button className="admin-primary-action" type="button" disabled={Boolean(reviewAction)} onClick={() => beginReview(mapping, "approve")}><CheckCircle2 size={17} />Hasil Benar</button>
        <button className="row-action-button" type="button" disabled={Boolean(reviewAction)} onClick={() => beginReview(mapping, "correct")}><PencilLine size={17} />Perbaiki Hasil</button>
        <button className="row-action-button admin-reject-action" type="button" disabled={Boolean(reviewAction)} onClick={() => beginReview(mapping, "reject")}><XCircle size={17} />Bukan Evidence</button>
      </div>

      {reviewIntent ? (
        <form className="admin-review-form" onSubmit={(event) => { event.preventDefault(); submitReview(); }}>
          <div className="admin-review-form-heading">
            <div><strong>{decisionLabels[reviewIntent.decision]}</strong><p>Lengkapi catatan singkat agar keputusan dapat ditelusuri kembali.</p></div>
            <button type="button" className="text-action-button" onClick={cancelReview}>Batal</button>
          </div>
          <label>Nama atau identitas pemeriksa<input autoFocus value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="Contoh: Siti Rahma / NIP" /></label>
          {reviewIntent.decision === "correct" ? (
            <div className="admin-correction-grid">
              <label>Parameter yang benar
                <select value={correctionTarget} onChange={(event) => setCorrectionTarget(event.target.value)}>
                  {mappings.map((item) => <option key={item.id} value={`${item.kk_id}|${item.kode}|${item.detail_kode}`}>{item.kk_id} — {item.kk_title || "KK"} › {item.kode} {item.subunsur_name || "Subunsur"} › {item.detail_kode} {item.parameter_uraian || "Parameter terkait"}</option>)}
                </select>
              </label>
              <label>Grade hasil pemeriksaan
                <select value={correctionGrade} onChange={(event) => setCorrectionGrade(event.target.value)}>
                  <option value="">Belum ditetapkan</option>
                  {['E', 'D', 'C', 'B', 'A'].map((grade) => <option value={grade} key={grade}>Grade {grade}</option>)}
                </select>
              </label>
            </div>
          ) : null}
          <label>Catatan pemeriksaan<textarea value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="Tuliskan alasan singkat berdasarkan isi dokumen" /></label>
          <button className="admin-primary-action" type="submit" disabled={Boolean(reviewAction)}>{reviewAction ? <Loader2 className="spin" size={17} /> : <CheckCircle2 size={17} />}Simpan Keputusan</button>
        </form>
      ) : null}

      {reviewMessage ? <div className="admin-review-message" role="status">{reviewMessage}</div> : null}

      {mappings.length > 1 ? (
        <details className="admin-alternative-results">
          <summary>Lihat {mappings.length - 1} kemungkinan parameter lainnya</summary>
          <div>
            {mappings.filter((item) => item.id !== mapping.id).map((item) => (
              <article key={item.id}>
                <div><strong>{item.kk_id} · {item.kk_title || "KK"}</strong><p>Subunsur {item.kode}: {item.subunsur_name || item.matrix_subunsur_name || "belum tersedia"}</p><p>Parameter {item.detail_kode}: {item.parameter_uraian || "uraian belum tersedia"}</p></div>
                <span>{confidenceLabel(item.mapping_score)} · {Math.round((item.mapping_score ?? 0) * 100)}%</span>
              </article>
            ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}
