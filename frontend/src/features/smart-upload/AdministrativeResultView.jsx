import { CheckCircle2, Info, Loader2, PencilLine, XCircle } from "lucide-react";
import { EmptyState } from "../shared/Feedback.jsx";
import {
  administrativeReviewGroups,
  administrativeRunStatus,
  correctionCatalogSelection,
  correctionTargetFor,
  decisionConfidence,
  documentFamilyPresentation,
} from "./admin-result.js";

export default function AdministrativeResultView({
  run,
  documentFamily,
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
  correctionCatalog,
  correctionCatalogLoading,
  correctionCatalogError,
  reloadCorrectionCatalog,
  reviewAction,
  reviewMessage,
  beginReview,
  submitReview,
  cancelReview,
}) {
  const family = documentFamilyPresentation(documentFamily);
  const familyReasons = documentFamily?.reasons ?? [];
  const familyWarnings = documentFamily?.warnings ?? [];
  const relationshipHints = documentFamily?.relationship_hints ?? [];
  const referencedTypes = relationshipHints.flatMap((item) => item.referenced_document_types ?? []);
  const referencedPeriod = relationshipHints.find((item) => item.referenced_period)?.referenced_period;
  if (!primaryResult) {
    return (
      <section className="admin-result-view">
        <div className="admin-result-intro">
          <div><span className="admin-eyebrow">Hasil pengenalan fungsi dokumen</span><h4>{family.familyLabel}</h4><p>Sistem menentukan jenis dan fungsi dokumen sebelum mencari parameter penilaian.</p></div>
          <span className="admin-status-badge">{administrativeRunStatus(run)}</span>
        </div>
        <div className="admin-family-summary">
          <div><span>Jenis dokumen</span><strong>{family.familyLabel}</strong><small>Confidence jenis dokumen {Math.round(family.familyConfidence * 100)}%</small></div>
          <div><span>Fungsi evidence</span><strong>{family.evidenceRoleLabel}</strong><small>Menentukan kewenangan dokumen dalam penilaian</small></div>
          <div><span>Coverage relevan</span><strong>{Math.round(family.relevantCoverage * 100)}%</strong><small>Bagian penting yang berhasil dibaca</small></div>
          <div><span>Parameter utama</span><strong>Tidak mempunyai parameter utama</strong><small>Dokumen yang dirujuk perlu dianalisis terpisah</small></div>
          <div><span>Status Grade</span><strong>{family.gradeStatus === "not_applicable" ? "Grade tidak berlaku untuk jenis dokumen ini" : "Belum dapat dinilai"}</strong><small>Tidak ada Grade mandiri yang dipaksakan</small></div>
        </div>
        {referencedTypes.length ? (
          <div className="admin-reference-panel"><strong>Dokumen yang dirujuk</strong><p>{[...new Set(referencedTypes)].map((item) => item.replaceAll("_", " ")).join(", ")}{referencedPeriod ? ` · periode ${referencedPeriod}` : ""}</p></div>
        ) : null}
        {familyReasons.length ? <div className="admin-detected-panel"><div><strong>Alasan utama</strong></div><ul>{familyReasons.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        {familyWarnings.length ? <div className="admin-confirmation-panel"><div><strong>Yang perlu diperiksa</strong></div><ul>{familyWarnings.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        {!familyReasons.length && !familyWarnings.length ? <EmptyState text="Belum ada parameter yang cukup didukung oleh isi dokumen." /> : null}
      </section>
    );
  }

  const { mapping, assessment, verifications, direction } = primaryResult;
  const confidence = decisionConfidence(mapping);
  const reviewGroups = administrativeReviewGroups({ run, assessment, verifications });
  const hasReviewItems = Object.values(reviewGroups).some((items) => items.length);
  const parameterLabel = mapping.parameter_uraian || mapping.subunsur_name || "Uraian parameter belum tersedia";
  const evidenceGrade = assessment.candidate_grade && assessment.grade_status === "supported"
    ? `Grade ${assessment.candidate_grade}`
    : "Belum dapat ditetapkan";
  const documentRole = family.evidenceRoleLabel;
  const subunsurLabel = mapping.subunsur_name || mapping.matrix_subunsur_name || "Belum ditemukan";
  const correctionSelection = correctionCatalogSelection(
    correctionCatalog,
    correctionTarget,
  );
  const correctionCatalogUnavailable = reviewIntent?.decision === "correct"
    && (correctionCatalogLoading || !correctionCatalog.length);
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

      <div className="admin-family-summary">
        <div><span>Jenis dokumen</span><strong>{family.familyLabel}</strong><small>Confidence jenis dokumen {Math.round(family.familyConfidence * 100)}%</small></div>
        <div><span>Fungsi evidence</span><strong>{documentRole}</strong><small>Wewenang dokumen terhadap parameter dan Grade</small></div>
        <div><span>Coverage relevan</span><strong>{Math.round(family.relevantCoverage * 100)}%</strong><small>Bagian penting yang berhasil dibaca</small></div>
      </div>

      <div className="admin-classification-path" aria-label="Hasil klasifikasi dokumen">
        <div><span>KK yang ditemukan</span><strong>{mapping.kk_id}</strong><small>{mapping.kk_title || "Nama KK belum tersedia"}</small></div>
        <div><span>Unsur</span><strong>{mapping.unsur || mapping.matrix_subunsur_name || "Belum ditemukan"}</strong></div>
        <div><span>Subunsur</span><strong>{mapping.kode}</strong><small>{subunsurLabel}</small></div>
        <div><span>Parameter</span><strong>{mapping.detail_kode}</strong><small>{parameterLabel}</small></div>
      </div>

      <div className={`admin-document-role admin-document-role-${family.evidenceRole || mapping.document_role || "unknown"}`}>
        <strong>Peran dokumen: {documentRole}</strong>
        <span>{mapping.document_role === "primary"
          ? "Isi dokumen dapat diperiksa sebagai bukti pelaksanaan, hasil, evaluasi, atau tindak lanjut."
          : "Dokumen ini membantu menemukan parameter, tetapi Grade memerlukan bukti utama yang dirujuk atau dilampirkan."}</span>
      </div>

      <div className="admin-result-metrics">
        <div>
          <span>Confidence Keputusan</span>
          <strong>{confidence.label} · {Math.round(confidence.score * 100)}%</strong>
          <small>Sudah dikalibrasi dengan family, coverage, fakta, dan margin kandidat</small>
        </div>
        <div className="grade-direction">
          <span>Arah Grade</span>
          <strong>{direction.label}</strong>
          <small>{direction.basis === "not_applicable" ? "Dokumen tidak mempunyai Grade mandiri" : direction.basis === "blocked" ? "Parameter atau isi belum cukup terkonfirmasi" : "Perkiraan awal, bukan keputusan akhir"}</small>
        </div>
        <div>
          <span>Grade yang sudah terbukti</span>
          <strong>{evidenceGrade}</strong>
          <small>Berdasarkan syarat yang berhasil ditemukan</small>
        </div>
      </div>

      {mapping.decision_status === "ambiguous" ? (
        <div className="admin-confirmation-panel"><div><strong>Kandidat parameter masih ambigu</strong></div><ul><li>Selisih kandidat pertama dan kedua terlalu kecil; pemeriksa perlu memilih parameter yang benar.</li></ul></div>
      ) : null}

      {familyWarnings.length ? (
        <div className="admin-confirmation-panel"><div><strong>Peringatan jenis dokumen atau coverage</strong></div><ul>{familyWarnings.map((item) => <li key={item}>{item}</li>)}</ul></div>
      ) : null}

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
            <div className="admin-correction-catalog">
              <div className="admin-correction-catalog-heading">
                <div><strong>Katalog penilaian lengkap</strong><p>Pilih berurutan dari seluruh KK, subunsur, parameter, lalu Grade.</p></div>
                {correctionCatalog.length ? <span>{correctionCatalog.length} parameter</span> : null}
              </div>
              {correctionCatalogLoading ? <div className="admin-catalog-status"><Loader2 className="spin" size={17} />Memuat seluruh katalog penilaian…</div> : null}
              {correctionCatalogError ? (
                <div className="admin-catalog-error" role="alert"><span>{correctionCatalogError}</span><button type="button" className="text-action-button" onClick={reloadCorrectionCatalog}>Coba lagi</button></div>
              ) : null}
              {correctionCatalog.length ? (
                <div className="admin-correction-grid">
                  <label>1. Kelompok Kinerja (KK)
                    <select
                      value={correctionSelection.activeKk}
                      onChange={(event) => {
                        setCorrectionTarget(correctionTargetFor(correctionCatalog, { kkId: event.target.value }));
                        setCorrectionGrade("");
                      }}
                    >
                      {correctionSelection.kkOptions.map((item) => <option key={item.kk_id} value={item.kk_id}>{item.kk_id} — {item.kk_title || "Nama KK belum tersedia"}</option>)}
                    </select>
                  </label>
                  <label>2. Subunsur
                    <select
                      value={correctionSelection.activeKode}
                      onChange={(event) => {
                        setCorrectionTarget(correctionTargetFor(correctionCatalog, { kkId: correctionSelection.activeKk, kode: event.target.value }));
                        setCorrectionGrade("");
                      }}
                    >
                      {correctionSelection.subunsurOptions.map((item) => <option key={`${item.kk_id}-${item.kode}`} value={item.kode}>{item.kode} — {item.subunsur_name || item.matrix_subunsur_name || "Nama subunsur belum tersedia"}</option>)}
                    </select>
                  </label>
                  <label>3. Parameter
                    <select
                      value={correctionSelection.target}
                      onChange={(event) => {
                        setCorrectionTarget(event.target.value);
                        setCorrectionGrade("");
                      }}
                    >
                      {correctionSelection.parameterOptions.map((item) => <option key={`${item.kk_id}-${item.detail_kode}`} value={`${item.kk_id}|${item.kode}|${item.detail_kode}`}>{item.detail_kode} — {item.uraian || "Uraian parameter belum tersedia"}</option>)}
                    </select>
                  </label>
                  <label>4. Grade hasil pemeriksaan
                    <select value={correctionGrade} onChange={(event) => setCorrectionGrade(event.target.value)}>
                      <option value="">Belum ditetapkan</option>
                      {correctionSelection.gradeOptions.map((grade) => <option value={grade} key={grade}>Grade {grade}</option>)}
                    </select>
                  </label>
                </div>
              ) : null}
            </div>
          ) : null}
          <label>Catatan pemeriksaan<textarea value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="Tuliskan alasan singkat berdasarkan isi dokumen" /></label>
          <button className="admin-primary-action" type="submit" disabled={Boolean(reviewAction) || correctionCatalogUnavailable}>{reviewAction ? <Loader2 className="spin" size={17} /> : <CheckCircle2 size={17} />}Simpan Keputusan</button>
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
                <span>{decisionConfidence(item).label} · {Math.round(decisionConfidence(item).score * 100)}%</span>
              </article>
            ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}
