import assert from "node:assert/strict";
import {
  administrativeMissingItems,
  administrativeReviewGroups,
  administrativeRunStatus,
  confidenceLabel,
  gradeDirection,
  primaryAdministrativeResult,
} from "../src/features/smart-upload/admin-result.js";

assert.equal(confidenceLabel(0.9), "Tinggi");
assert.equal(confidenceLabel(0.7), "Sedang");
assert.equal(administrativeRunStatus({ status: "review_required", coverage_status: "partial" }), "Perlu melengkapi informasi");
assert.equal(gradeDirection({ candidate_grade: "C", primary_allowed: true }).label, "Grade C");
assert.equal(gradeDirection({ candidate_grade: "C", primary_allowed: false }).label, "Mendekati Grade C");
assert.deepEqual(
  gradeDirection({
    rule_trace: {
      rules: [
        { grade: "E", missing_requirements: ["period:required_single"] },
        { grade: "D", missing_requirements: ["stage:socialization", "source_type:socialization_record"] },
      ],
    },
  }),
  { grade: "E", label: "Mendekati Grade E", basis: "administrative_gap" },
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
