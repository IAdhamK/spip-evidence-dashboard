import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
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
import {
  parameterSourceLabel,
  sheetStatusLabel,
  workbookEvidenceSummary,
  workbookReviewState,
} from "../src/features/smart-upload/workbook-evidence.js";

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
  correctionCatalogSelection([], ""),
  {
    kkOptions: [],
    subunsurOptions: [],
    parameterOptions: [],
    selectedParameter: null,
    activeKk: "",
    activeKode: "",
    target: "",
    gradeOptions: [],
  },
);
assert.equal(
  correctionTargetFor([null, undefined], { kkId: "KK3.1" }),
  "",
);
assert.equal(correctionCatalogSelection(null, "").target, "");
assert.equal(correctionTargetFor(null, { kkId: "KK3.1" }), "");

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

const workbookFixture = {
  applicable: true,
  is_multi_evidence: true,
  status: "ready",
  sheet_count: 3,
  substantive_sheet_count: 2,
  sheet_results: [
    {
      sheet_key: "sheet:register-risiko",
      sheet_name: "Register Risiko",
      status: "mapped",
      evidence_role: "primary",
      coverage_status: "complete",
      fact_count: 4,
      primary_parameter: {
        kk_id: "KK3.1",
        kode: "2.1",
        detail_kode: "2.1.2",
        uraian: "Risiko dituangkan dalam register risiko",
        mapping_score: 0.91,
        verification_status: "verified",
      },
      secondary_parameters: [
        { kk_id: "KK3.1", kode: "2.2", detail_kode: "2.2.1", mapping_score: 0.80 },
        { kk_id: "KK3.1", kode: "2.2", detail_kode: "2.2.2", mapping_score: 0.79 },
        { kk_id: "KK3.1", kode: "2.2", detail_kode: "2.2.3", mapping_score: 0.78 },
        { kk_id: "KK3.1", kode: "2.2", detail_kode: "2.2.4", mapping_score: 0.77 },
      ],
      warnings: [],
    },
    {
      sheet_key: "sheet:analisis-risiko",
      sheet_name: "Analisis Risiko",
      status: "ambiguous",
      evidence_role: "supporting",
      coverage_status: "complete",
      fact_count: 3,
      primary_parameter: {
        kk_id: "KK3.1",
        kode: "2.2",
        detail_kode: "2.2.1",
        mapping_score: 0.73,
        verification_status: "needs_human_review",
      },
      secondary_parameters: [],
      warnings: ["Dua kandidat teratas mempunyai dukungan yang berdekatan."],
    },
    {
      sheet_key: "sheet:template",
      sheet_name: "Template",
      status: "template_only",
      evidence_role: "context",
      coverage_status: "complete",
      fact_count: 0,
      primary_parameter: {
        kk_id: "KK3.1",
        kode: "2.1",
        detail_kode: "2.1.2",
      },
      secondary_parameters: [],
      warnings: ["Sheet hanya berisi template."],
    },
  ],
  workbook_summary: {
    primary_parameter: {
      kk_id: "KK3.1",
      kode: "2.1",
      detail_kode: "2.1.2",
      source_sheets: [{ sheet_name: "Register Risiko" }],
    },
    secondary_parameters: [],
    parameter_sources: [
      {
        kk_id: "KK3.1",
        kode: "2.1",
        detail_kode: "2.1.2",
        source_sheets: [{ sheet_name: "Register Risiko" }],
      },
      {
        kk_id: "KK3.1",
        kode: "2.2",
        detail_kode: "2.2.1",
        source_sheets: [{ sheet_name: "Analisis Risiko" }],
      },
    ],
    warnings: ["1 sheet mempunyai atribusi parameter ambigu."],
  },
};
const workbookModel = workbookEvidenceSummary(workbookFixture);
assert.equal(workbookModel.showPanel, true);
assert.equal(workbookModel.sheetCount, 3);
assert.equal(workbookModel.substantiveSheetCount, 2);
assert.equal(workbookModel.parameterCount, 2);
assert.equal(workbookModel.evidenceSheets.length, 2);
assert.equal(workbookModel.evidenceSheets[0].secondaryParameters.length, 3);
assert.equal(workbookModel.evidenceSheets[0].tone, "success");
assert.equal(workbookModel.evidenceSheets[1].tone, "warning");
assert.equal(workbookModel.excludedSheets[0].primaryParameter, null);
assert.equal(parameterSourceLabel(workbookFixture.workbook_summary.parameter_sources[0]), "Sumber: Sheet Register Risiko");
assert.equal(sheetStatusLabel("ambiguous"), "Perlu dipastikan");
assert.equal(workbookReviewState(workbookFixture).canConfirmWorkbookAtOnce, false);
assert.equal(
  workbookReviewState(workbookFixture).message,
  "Periksa dan konfirmasi setiap sheet yang relevan melalui Review Terpandu.",
);

const singleEvidence = workbookEvidenceSummary({
  ...workbookFixture,
  is_multi_evidence: false,
  sheet_count: 1,
  substantive_sheet_count: 1,
  sheet_results: workbookFixture.sheet_results.slice(0, 1),
});
assert.equal(singleEvidence.showPanel, false);
assert.deepEqual(workbookEvidenceSummary(), {
  applicable: false,
  isMultiEvidence: false,
  showPanel: false,
  status: "not_available",
  sheetCount: 0,
  substantiveSheetCount: 0,
  parameterCount: 0,
  primaryParameter: null,
  secondaryParameters: [],
  parameterSources: [],
  evidenceSheets: [],
  excludedSheets: [],
  warnings: [],
  review: {
    showPanel: false,
    hasAmbiguousSheets: false,
    hasBlockedSheets: false,
    hasPendingRetrieval: false,
    requiresPerSheetReview: false,
    canConfirmWorkbookAtOnce: true,
    message: "Periksa dan konfirmasi setiap sheet yang relevan melalui Review Terpandu.",
  },
});

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const intelligenceSource = readFileSync(resolve(scriptDirectory, "../src/features/smart-upload/DocumentIntelligenceResult.jsx"), "utf8");
const workbookPanelSource = readFileSync(resolve(scriptDirectory, "../src/features/smart-upload/WorkbookEvidencePanel.jsx"), "utf8");
const stylesSource = readFileSync(resolve(scriptDirectory, "../src/styles/main.css"), "utf8");
assert.match(intelligenceSource, /<WorkbookEvidencePanel evidence=\{snapshot\.workbook_evidence\}/);
assert.match(intelligenceSource, /<AdministrativeResultView/);
assert.match(workbookPanelSource, /Workbook Multi-Evidence/);
assert.match(workbookPanelSource, /Buka Detail Pemeriksaan/);
assert.match(stylesSource, /\.workbook-evidence-panel[^}]*max-width:\s*100%/s);
assert.match(stylesSource, /\.workbook-evidence-panel\s+:where\([^}]*overflow-wrap:\s*anywhere/s);
assert.match(stylesSource, /\.workbook-aggregate-list,\s*\.workbook-secondary-list\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);

console.log("Administrative result checks passed.");
