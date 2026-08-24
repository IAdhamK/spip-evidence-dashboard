import assert from "node:assert/strict";
import {
  gradeCodeItems,
  deriveParameterEvidenceStatus,
  normalizeStaticSnapshotEvidence,
  parameterCodeItems,
  parameterEvidenceRule,
} from "../src/lib/parameter-evidence.js";

const spipOnly = { kode_spip: "SPIP", kode_mri: "-", kode_iepk: "-" };
assert.deepEqual(parameterCodeItems(spipOnly), [{ system: "SPIP", value: "SPIP" }]);
assert.equal(parameterEvidenceRule(spipOnly).mode, "sequential_from_e");

const withMri = { kode_spip: "SPIP", kode_mri: "MRI", kode_iepk: "-" };
assert.deepEqual(parameterCodeItems(withMri).map((item) => item.system), ["SPIP", "MRI"]);
assert.equal(parameterEvidenceRule(withMri).mode, "single_grade");

const withIepk = { kode_spip: "SPIP", kode_mri: "-", kode_iepk: "IEPK" };
assert.equal(parameterEvidenceRule(withIepk).mode, "single_grade");

assert.deepEqual(
  gradeCodeItems({ kode_parameter: { spip: "SPIP", mri: "-", iepk: "IEPK" } }).map((item) => item.system),
  ["SPIP", "IEPK"],
);

const spipProgress = {
  ...spipOnly,
  detail_kode: "1.1.1",
  grades: ["A", "B", "C", "D", "E"].map((grade) => ({
    grade,
    evidence_folders: [{ grade, file_count: ["E", "D", "C"].includes(grade) ? 1 : 0 }],
  })),
};
assert.equal(deriveParameterEvidenceStatus(spipProgress).status, "Terisi Penuh");

const gapProgress = structuredClone(spipProgress);
gapProgress.grades.find((grade) => grade.grade === "D").evidence_folders[0].file_count = 0;
assert.equal(deriveParameterEvidenceStatus(gapProgress).status, "Terisi Sebagian");

const mriProgress = structuredClone({ ...spipProgress, ...withMri });
mriProgress.grades.forEach((grade) => { grade.evidence_folders[0].file_count = grade.grade === "B" ? 1 : 0; });
assert.equal(deriveParameterEvidenceStatus(mriProgress).status, "Terisi Penuh");

const snapshot = normalizeStaticSnapshotEvidence({
  meta: {},
  dashboard: {
    folders: [{ kk_id: "KK3.1", kode: "1.1", file_count: 3, status: "Terisi" }],
    kk_summary: [{ kk_id: "KK3.1", status_counts: { Terisi: 1 } }],
  },
  kk: [{ id: "KK3.1", folders: [{ kk_id: "KK3.1", kode: "1.1", status: "Terisi" }] }],
  subunsur_details: {
    "KK3.1::1.1": {
      kk_id: "KK3.1",
      kode: "1.1",
      file_count: 3,
      parameters: [structuredClone(spipProgress)],
    },
  },
});
assert.equal(snapshot.dashboard.folders[0].status, "Terisi Penuh");
assert.deepEqual(snapshot.dashboard.status_counts, {
  Kosong: 0,
  "Terisi Sebagian": 0,
  "Terisi Penuh": 1,
});
assert.equal(snapshot.subunsur_details["KK3.1::1.1"].parameters[0].evidence_status, "Terisi Penuh");

console.log("Parameter evidence helper checks passed.");
