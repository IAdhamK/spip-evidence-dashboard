import assert from "node:assert/strict";
import {
  gradeCodeItems,
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

console.log("Parameter evidence helper checks passed.");
