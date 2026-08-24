const EMPTY_CODE_VALUES = new Set(["", "-", "–", "—"]);

function validCode(value) {
  const text = String(value ?? "").trim();
  return EMPTY_CODE_VALUES.has(text) ? "" : text;
}

export function parameterCodeItems(parameter = {}) {
  return [
    ["SPIP", validCode(parameter.kode_spip)],
    ["MRI", validCode(parameter.kode_mri)],
    ["IEPK", validCode(parameter.kode_iepk)],
  ]
    .filter(([, value]) => value)
    .map(([system, value]) => ({ system, value }));
}

export function gradeCodeItems(grade = {}) {
  const codes = grade.kode_parameter ?? {};
  return [
    ["SPIP", validCode(codes.spip)],
    ["MRI", validCode(codes.mri)],
    ["IEPK", validCode(codes.iepk)],
  ]
    .filter(([, value]) => value)
    .map(([system, value]) => ({ system, value }));
}

export function parameterEvidenceRule(parameter = {}) {
  const systems = parameterCodeItems(parameter).map((item) => item.system);
  if (systems.includes("MRI") || systems.includes("IEPK")) {
    return {
      mode: "single_grade",
      title: "Cukup satu Grade",
      description: "Satu evidence pada salah satu Grade sudah memenuhi parameter ini.",
    };
  }
  return {
    mode: "sequential_from_e",
    title: "Berurutan mulai Grade E",
    description: "Isi evidence mulai Grade E secara berurutan sampai grade tertinggi yang telah dicapai; tidak wajib sampai Grade A.",
  };
}
