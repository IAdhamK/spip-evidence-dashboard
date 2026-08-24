import { deriveParameterEvidenceStatus } from "../src/lib/parameter-evidence.js";

const STATUS_ORDER = ["Kosong", "Terisi Sebagian", "Terisi Penuh"];

function slotKey(slot = {}) {
  return [slot.detail_kode, slot.grade, slot.category_name, slot.category_folder].join("::");
}

export function recalculateDetail(detail, scanned) {
  const scannedAt = scanned.scannedAt;
  const slotsByKey = new Map(scanned.slots.map((slot) => [slotKey(slot), slot]));
  const evidenceSlots = (detail.evidence_slots ?? []).map((slot) => ({
    ...slot,
    ...(slotsByKey.get(slotKey(slot)) ?? {}),
  }));

  const parameters = (detail.parameters ?? []).map((parameter) => {
    const grades = (parameter.grades ?? []).map((grade) => ({
      ...grade,
      evidence_folders: evidenceSlots.filter(
        (slot) => slot.detail_kode === parameter.detail_kode && slot.grade === grade.grade,
      ),
    }));
    const updated = { ...parameter, grades };
    const result = deriveParameterEvidenceStatus(updated);
    return {
      ...updated,
      evidence_status: result.status,
      evidence_status_reason: result.reason,
      active_codes: result.activeCodes,
      evidence_rule: result.ruleMode,
      filled_grades: result.filledGrades,
      missing_grades_before_highest: result.missingGrades,
      highest_filled_grade: result.highestFilledGrade,
      evidence_file_count: result.evidenceFileCount,
    };
  });

  const directFileCount = scanned.rootItems.filter((item) => !item.is_folder).length;
  const attributedCount = evidenceSlots.reduce(
    (total, slot) => total + Math.max(Number(slot.file_count) || 0, 0),
    0,
  );
  const totalEvidence = directFileCount + attributedCount;
  const fullCount = parameters.filter((parameter) => parameter.evidence_status === "Terisi Penuh").length;
  const hasSlotError = evidenceSlots.some((slot) => String(slot.error_message ?? "").trim());

  let status = "Terisi Sebagian";
  let statusReason = "Status belum dapat dipastikan karena sinkronisasi belum lengkap.";
  if (hasSlotError) {
    statusReason = "Status belum dapat dipastikan karena sebagian folder Grade gagal dibaca.";
  } else if (directFileCount > 0) {
    statusReason = `Ada ${directFileCount} file yang belum ditempatkan pada Grade parameter.`;
  } else if (!parameters.length) {
    status = totalEvidence ? "Terisi Sebagian" : "Kosong";
    statusReason = totalEvidence
      ? "Evidence ditemukan, tetapi parameter belum tersedia untuk menentukan kelengkapannya."
      : "Parameter belum tersedia dan belum ada evidence.";
  } else if (totalEvidence === 0) {
    status = "Kosong";
    statusReason = "Belum ada evidence pada seluruh parameter subunsur ini.";
  } else if (fullCount === parameters.length) {
    status = "Terisi Penuh";
    statusReason = `Seluruh ${fullCount} parameter telah memenuhi aturan evidence kode parameternya.`;
  } else {
    statusReason = `${fullCount} dari ${parameters.length} parameter sudah penuh; ${parameters.length - fullCount} parameter masih perlu dilengkapi atau diperiksa.`;
  }

  const files = [...scanned.rootItems, ...scanned.slotFiles];
  const totalSizeBytes = files
    .filter((item) => !item.is_folder)
    .reduce((total, item) => total + Math.max(Number(item.size_bytes) || 0, 0), 0);

  return {
    ...detail,
    parameters,
    evidence_slots: evidenceSlots,
    files,
    file_count: totalEvidence,
    total_size_bytes: totalSizeBytes,
    status,
    status_reason: statusReason,
    last_scanned_at: scannedAt,
    error_message: null,
  };
}

export function applyDetailToDashboard(dashboard, detail) {
  const folders = (dashboard.folders ?? []).map((folder) =>
    folder.kk_id === detail.kk_id && folder.kode === detail.kode
      ? folderSummary(detail)
      : folder,
  );
  const statusCounts = countStatuses(folders);
  const kkSummary = (dashboard.kk_summary ?? []).map((summary) => {
    const kkFolders = folders.filter((folder) => folder.kk_id === summary.kk_id);
    return {
      ...summary,
      file_count: sum(kkFolders, "file_count"),
      total_size_bytes: sum(kkFolders, "total_size_bytes"),
      status_counts: countStatuses(kkFolders),
    };
  });
  return {
    ...dashboard,
    folders,
    total_files: sum(folders, "file_count"),
    total_size_bytes: sum(folders, "total_size_bytes"),
    status_counts: statusCounts,
    kk_summary: kkSummary,
  };
}

export function applyDetailToKk(kk, detail) {
  return {
    ...kk,
    folders: (kk.folders ?? []).map((folder) =>
      folder.kk_id === detail.kk_id && folder.kode === detail.kode
        ? folderSummary(detail)
        : folder,
    ),
  };
}

function folderSummary(detail) {
  const {
    parameters: _parameters,
    evidence_slots: _evidenceSlots,
    files: _files,
    matrix_subunsur_name: _matrixName,
    ...summary
  } = detail;
  return summary;
}

function countStatuses(folders) {
  return Object.fromEntries(
    STATUS_ORDER.map((status) => [
      status,
      folders.filter((folder) => folder.status === status).length,
    ]),
  );
}

function sum(items, key) {
  return items.reduce((total, item) => total + Math.max(Number(item[key]) || 0, 0), 0);
}
