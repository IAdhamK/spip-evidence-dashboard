const EMPTY_CODE_VALUES = new Set(["", "-", "–", "—"]);
const GRADE_SEQUENCE = ["E", "D", "C", "B", "A"];

const STATUS_EXPLANATIONS = {
  Kosong: "Belum ada file evidence pada folder ini.",
  "Terisi Sebagian": "Sudah ada evidence, tetapi belum seluruh parameter memenuhi aturan kode SPIP/MRI/IEPK.",
  "Terisi Penuh": "Seluruh parameter telah memenuhi aturan evidence sesuai jenis kode parameternya.",
};

function validCode(value) {
  const text = String(value ?? "").trim();
  return EMPTY_CODE_VALUES.has(text) ? "" : text;
}

export function isMissingFolderError(message) {
  const text = String(message ?? "").trim().toLowerCase();
  return Boolean(text) && (
    text.includes("http 404 not found")
    || text.includes("sabredav\\exception\\notfound")
    || text.includes("could not be located")
  );
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

export function deriveParameterEvidenceStatus(parameter = {}) {
  const codes = parameterCodeItems(parameter).map((item) => item.system);
  const gradeFolders = new Map(
    (parameter.grades ?? []).map((grade) => [
      String(grade.grade ?? "").trim().toUpperCase(),
      grade.evidence_folders ?? [],
    ]),
  );
  const fileCountForGrade = (grade) => (gradeFolders.get(grade) ?? [])
    .reduce((total, folder) => total + Math.max(Number(folder.file_count) || 0, 0), 0);
  const filledGrades = GRADE_SEQUENCE.filter((grade) => fileCountForGrade(grade) > 0);
  const evidenceFileCount = GRADE_SEQUENCE.reduce((total, grade) => total + fileCountForGrade(grade), 0);
  const hasScanError = [...gradeFolders.values()].some((folders) =>
    folders.some((folder) => {
      const errorMessage = String(folder.error_message ?? "").trim();
      if (!errorMessage) return false;
      return (Number(folder.file_count) || 0) > 0 || !isMissingFolderError(errorMessage);
    }),
  );
  const hasAlternativeCode = codes.includes("MRI") || codes.includes("IEPK");
  const highestFilledGrade = filledGrades.at(-1) ?? null;
  const highestIndex = highestFilledGrade ? GRADE_SEQUENCE.indexOf(highestFilledGrade) : -1;
  const missingGrades = highestIndex >= 0
    ? GRADE_SEQUENCE.slice(0, highestIndex + 1).filter((grade) => !filledGrades.includes(grade))
    : [];

  let status = "Kosong";
  let reason = "Belum ada evidence pada Grade parameter ini.";
  if (hasScanError) {
    status = "Terisi Sebagian";
    reason = "Pemeriksaan folder Grade belum lengkap karena ada folder yang gagal dibaca.";
  } else if (filledGrades.length && !codes.length) {
    status = "Terisi Sebagian";
    reason = "Evidence ditemukan, tetapi jenis kode parameter belum dapat ditentukan.";
  } else if (filledGrades.length && hasAlternativeCode) {
    status = "Terisi Penuh";
    reason = `Kode ${codes.join("/")} terpenuhi oleh evidence pada minimal satu Grade (${filledGrades.join(", ")}).`;
  } else if (filledGrades.length && missingGrades.length) {
    status = "Terisi Sebagian";
    reason = `Evidence SPIP belum berurutan mulai Grade E; lengkapi Grade ${missingGrades.join(", ")} sebelum ${highestFilledGrade}.`;
  } else if (filledGrades.length) {
    status = "Terisi Penuh";
    reason = `Evidence SPIP sudah berurutan mulai Grade E sampai Grade ${highestFilledGrade}.`;
  }

  return {
    status,
    reason,
    activeCodes: codes,
    ruleMode: hasAlternativeCode ? "single_grade" : "sequential_from_e",
    filledGrades,
    missingGrades,
    highestFilledGrade,
    evidenceFileCount,
    hasScanError,
  };
}

export function normalizeStaticSnapshotEvidence(snapshot = {}) {
  const details = Object.values(snapshot.subunsur_details ?? {});
  const statusByKey = new Map();

  for (const detail of details) {
    const parameters = detail.parameters ?? [];
    const results = parameters.map((parameter) => {
      const result = deriveParameterEvidenceStatus(parameter);
      Object.assign(parameter, {
        evidence_status: result.status,
        evidence_status_reason: result.reason,
        active_codes: result.activeCodes,
        evidence_rule: result.ruleMode,
        filled_grades: result.filledGrades,
        missing_grades_before_highest: result.missingGrades,
        highest_filled_grade: result.highestFilledGrade,
        evidence_file_count: result.evidenceFileCount,
      });
      return result;
    });
    const attributedCount = results.reduce((total, result) => total + result.evidenceFileCount, 0);
    const unassignedCount = Math.max((Number(detail.file_count) || 0) - attributedCount, 0);
    const fullCount = results.filter((result) => result.status === "Terisi Penuh").length;
    const totalEvidence = attributedCount + unassignedCount;
    let status = "Terisi Sebagian";
    let reason = "Status belum dapat dipastikan karena sinkronisasi terakhir gagal.";

    if (!String(detail.error_message ?? "").trim() && results.some((result) => result.hasScanError)) {
      reason = "Status belum dapat dipastikan karena sebagian folder Grade gagal dibaca.";
    } else if (!String(detail.error_message ?? "").trim() && unassignedCount > 0) {
      reason = `Ada ${unassignedCount} file yang belum ditempatkan pada Grade parameter.`;
    } else if (!String(detail.error_message ?? "").trim() && !parameters.length) {
      status = totalEvidence ? "Terisi Sebagian" : "Kosong";
      reason = totalEvidence
        ? "Evidence ditemukan, tetapi parameter belum tersedia untuk menentukan kelengkapannya."
        : "Parameter belum tersedia dan belum ada evidence.";
    } else if (!String(detail.error_message ?? "").trim() && totalEvidence === 0) {
      status = "Kosong";
      reason = "Belum ada evidence pada seluruh parameter subunsur ini.";
    } else if (!String(detail.error_message ?? "").trim() && fullCount === results.length) {
      status = "Terisi Penuh";
      reason = `Seluruh ${fullCount} parameter telah memenuhi aturan evidence kode parameternya.`;
    } else if (!String(detail.error_message ?? "").trim()) {
      reason = `${fullCount} dari ${results.length} parameter sudah penuh; ${results.length - fullCount} parameter masih perlu dilengkapi atau diperiksa.`;
    }

    detail.status = status;
    detail.status_reason = reason;
    statusByKey.set(`${detail.kk_id}::${detail.kode}`, { status, reason });
  }

  const applyStatus = (folder) => {
    const derived = statusByKey.get(`${folder.kk_id}::${folder.kode}`);
    if (derived) {
      folder.status = derived.status;
      folder.status_reason = derived.reason;
    }
  };
  (snapshot.dashboard?.folders ?? []).forEach(applyStatus);
  (snapshot.kk ?? []).forEach((kk) => (kk.folders ?? []).forEach(applyStatus));

  if (snapshot.dashboard) {
    const folders = snapshot.dashboard.folders ?? [];
    snapshot.dashboard.status_counts = Object.fromEntries(
      ["Kosong", "Terisi Sebagian", "Terisi Penuh"].map((status) => [
        status,
        folders.filter((folder) => folder.status === status).length,
      ]),
    );
    for (const summary of snapshot.dashboard.kk_summary ?? []) {
      const kkFolders = folders.filter((folder) => folder.kk_id === summary.kk_id);
      summary.status_counts = Object.fromEntries(
        ["Kosong", "Terisi Sebagian", "Terisi Penuh"].map((status) => [
          status,
          kkFolders.filter((folder) => folder.status === status).length,
        ]),
      );
    }
  }
  snapshot.meta = { ...(snapshot.meta ?? {}), status_explanations: STATUS_EXPLANATIONS };
  return snapshot;
}
