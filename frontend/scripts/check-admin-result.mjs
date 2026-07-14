import assert from "node:assert/strict";
import {
  administrativeMissingItems,
  administrativeReviewGroups,
  administrativeRunStatus,
  confidenceLabel,
  correctionCatalogSelection,
  correctionTargetFor,
  decisionConfidence,
  documentFamilyPresentation,
  gradeDirection,
  primaryAdministrativeResult,
} from "../src/features/smart-upload/admin-result.js";

assert.equal(confidenceLabel(0.9), "Tinggi");
assert.equal(confidenceLabel(0.7), "Sedang");
assert.equal(administrativeRunStatus({ status: "review_required", coverage_status: "partial" }), "Perlu melengkapi informasi");
assert.equal(gradeDirection({ candidate_grade: "C", primary_allowed: true }).label, "Grade C");
assert.equal(gradeDirection({ candidate_grade: "C", primary_allowed: false }).label, "Mendekati Grade C");
assert.deepEqual(
  gradeDirection(
    { candidate_grade: "E", primary_allowed: false },
    { document_role: "supporting" },
  ),
  {
    grade: null,
    label: "Belum dapat ditentukan dari dokumen pendukung",
    basis: "non_primary_document",
  },
);
assert.deepEqual(
  gradeDirection({
    rule_trace: {
      rules: [
        { grade: "E", missing_requirements: ["period:required_single"] },
        { grade: "D", missing_requirements: ["stage:socialization", "source_type:socialization_record"] },
      ],
    },
  }),
  { grade: null, label: "Belum dapat dinilai", basis: "unavailable" },
);
assert.deepEqual(
  gradeDirection({ grade_status: "not_applicable", candidate_grade: null }),
  { grade: null, label: "Grade tidak berlaku untuk jenis dokumen ini", basis: "not_applicable" },
);
assert.deepEqual(
  gradeDirection({ grade_status: "blocked", candidate_grade: null }),
  { grade: null, label: "Belum dapat dinilai", basis: "blocked" },
);
assert.equal(
  gradeDirection({ grade_status: "direction_only", candidate_grade: "C", primary_allowed: true }).label,
  "Mendekati Grade C",
);
assert.equal(
  gradeDirection({ grade_status: "supported", candidate_grade: "C", primary_allowed: true }).label,
  "Grade C",
);
assert.deepEqual(
  decisionConfidence({ calibrated_decision_confidence: 0.59, decision_confidence_label: "ambiguous" }),
  { score: 0.59, label: "Ambigu" },
);
assert.equal(
  documentFamilyPresentation({ family: "transmittal_letter", evidence_role: "supporting" }).familyLabel,
  "Nota Dinas atau Surat Pengantar",
);
assert.equal(
  documentFamilyPresentation({ family: "transmittal_letter" }).gradeApplicable,
  false,
);
assert.equal(
  documentFamilyPresentation({ family: "unknown" }).gradeApplicable,
  true,
);
assert.equal(
  documentFamilyPresentation({ family: "unknown" }).gradeStatus,
  "blocked",
);

const primary = primaryAdministrativeResult({
  mappings: [
    { id: 1, mapping_score: 0.65 },
    { id: 2, mapping_score: 0.91 },
  ],
  assessments: [{ mapping_candidate_id: 2, candidate_grade: "E" }],
  verificationResults: [{ mapping_candidate_id: 2, period_ok: false }],
});
assert.equal(primary.mapping.id, 2);
assert.equal(primary.direction.grade, "E");

const correctionCatalog = [
  { kk_id: "KK3.1", kk_title: "Efektivitas", kode: "2.1", subunsur_name: "Identifikasi Risiko", detail_kode: "2.1.1", uraian: "Kebijakan risiko", available_grades: ["A", "B", "C", "D", "E"] },
  { kk_id: "KK3.1", kk_title: "Efektivitas", kode: "2.1", subunsur_name: "Identifikasi Risiko", detail_kode: "2.1.2", uraian: "Register risiko", available_grades: ["A", "B", "C", "D", "E"] },
  { kk_id: "KK3.1", kk_title: "Efektivitas", kode: "5.1", subunsur_name: "Pemantauan", detail_kode: "5.1.3", uraian: "Pemantauan risiko", available_grades: ["C", "B", "A"] },
  { kk_id: "KK3.2", kk_title: "Pelaporan Keuangan", kode: "2.1", subunsur_name: "Identifikasi Risiko", detail_kode: "2.1.2", uraian: "Register risiko keuangan", available_grades: ["E", "D"] },
  { kk_id: "KK3.3", kk_title: "Pengamanan Aset", kode: "2.1", subunsur_name: "Identifikasi Risiko", detail_kode: "2.1.2", uraian: "Register risiko aset", available_grades: ["E"] },
  { kk_id: "KK3.4", kk_title: "Ketaatan", kode: "2.1", subunsur_name: "Identifikasi Risiko", detail_kode: "2.1.2", uraian: "Register risiko ketaatan", available_grades: ["A"] },
];
const correctionSelection = correctionCatalogSelection(
  correctionCatalog,
  "KK3.1|5.1|5.1.3",
);
assert.equal(correctionSelection.kkOptions.length, 4);
assert.equal(correctionSelection.subunsurOptions.length, 2);
assert.equal(correctionSelection.selectedParameter.detail_kode, "5.1.3");
assert.deepEqual(correctionSelection.gradeOptions, ["C", "B", "A"]);
assert.equal(
  correctionTargetFor(correctionCatalog, { kkId: "KK3.2", kode: "2.1" }),
  "KK3.2|2.1|2.1.2",
);

assert.deepEqual(
  administrativeMissingItems({
    run: { coverage_status: "partial" },
    assessment: {
      missing_requirements: ["stage:implementation", "source_type:implementation_record", "period:required_single"],
      rule_trace: { approval_status: "draft" },
    },
    verifications: [{ period_ok: false, organization_ok: false }],
  }),
  [
    "Bagian dokumen yang belum terbaca",
    "Bukti pelaksanaan atau implementasi",
    "Konfirmasi tahun atau periode yang berlaku untuk penilaian",
    "Konfirmasi unit kerja atau organisasi yang dinilai",
    "Pedoman Grade belum disahkan; hasil tetap ditampilkan sebagai Arah Grade",
  ],
);

assert.deepEqual(
  administrativeReviewGroups({
    run: { coverage_status: "complete" },
    assessment: {
      missing_requirements: [],
      rule_trace: {
        context_resolution: {
          period: { values: ["2025"], inherited: true },
          organization: { values: ["Direktorat Jenderal Pembangunan Desa dan Perdesaan"], inherited: false },
        },
        rules: [{ approval_status: "draft" }],
      },
    },
    verifications: [{ period_ok: true, organization_ok: true, source_coverage_ok: true }],
  }),
  {
    missing: [],
    confirmations: [],
    detected: [
      "Periode 2025 ditemukan dalam konteks dokumen",
      "Unit kerja Direktorat Jenderal Pembangunan Desa dan Perdesaan ditemukan dalam konteks dokumen",
    ],
    governance: ["Pedoman Grade belum disahkan; hasil tetap ditampilkan sebagai Arah Grade"],
  },
);

console.log("Administrative result checks passed.");
