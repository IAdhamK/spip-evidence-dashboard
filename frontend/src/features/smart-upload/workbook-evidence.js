const MAX_SECONDARY_PARAMETERS = 3;
const EXCLUDED_SHEET_STATUSES = new Set([
  "empty",
  "hidden",
  "instruction_only",
  "no_substantive_facts",
  "not_processed",
  "template_only",
  "unreadable",
]);
const SUBSTANTIVE_SHEET_STATUSES = new Set([
  "ambiguous",
  "mapped",
  "needs_review",
  "needs_sheet_retrieval",
  "sheet_retrieval_recommended",
]);
const WARNING_SHEET_STATUSES = new Set([
  "ambiguous",
  "needs_review",
  "needs_sheet_retrieval",
  "sheet_retrieval_recommended",
]);
const DANGER_SHEET_STATUSES = new Set([
  "blocked",
  "failed",
  "rejected",
  "unreadable",
]);

function list(value) {
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

function uniqueStrings(values) {
  return [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))];
}

function parameterKey(parameter = {}) {
  return [parameter.kk_id, parameter.kode, parameter.detail_kode]
    .map((value) => String(value || "").trim())
    .join("|");
}

function sheetSourceName(source) {
  if (typeof source === "string") return source.trim();
  return String(source?.sheet_name || "").trim();
}

function prefixedSheetName(name) {
  return /^sheet\b/i.test(name) ? name : `Sheet ${name}`;
}

export function parameterSourceLabel(parameter = {}, fallbackSheetName = "") {
  const names = uniqueStrings([
    ...list(parameter?.source_sheets).map(sheetSourceName),
    fallbackSheetName,
  ]);
  if (!names.length) return "Sumber sheet belum tersedia";
  return `Sumber: ${names.map(prefixedSheetName).join(", ")}`;
}

export function sheetStatusLabel(status) {
  return ({
    ambiguous: "Perlu dipastikan",
    blocked: "Diblokir",
    empty: "Kosong",
    failed: "Gagal dibaca",
    hidden: "Tersembunyi",
    instruction_only: "Petunjuk saja",
    mapped: "Parameter ditemukan",
    needs_review: "Perlu diperiksa",
    needs_sheet_retrieval: "Perlu pencarian lanjutan",
    no_substantive_facts: "Belum ada bukti substantif",
    not_processed: "Belum diproses",
    rejected: "Ditolak",
    sheet_retrieval_recommended: "Rekomendasi perlu diperiksa",
    template_only: "Template saja",
    unreadable: "Tidak terbaca",
  })[String(status || "").toLowerCase()] || "Informasi sheet";
}

export function evidenceRoleLabel(role) {
  return ({
    context: "Konteks",
    optional: "Pelengkap",
    primary: "Evidence utama",
    reject: "Bukan evidence",
    supporting: "Evidence pendukung",
  })[String(role || "").toLowerCase()] || "Peran belum diketahui";
}

export function coverageStatusLabel(status) {
  return ({
    complete: "Lengkap terbaca",
    failed: "Gagal terbaca",
    partial: "Sebagian terbaca",
    pending: "Belum selesai",
  })[String(status || "").toLowerCase()] || "Belum diketahui";
}

export function verificationStatusLabel(status) {
  return ({
    blocked: "Diblokir",
    failed: "Gagal diverifikasi",
    mixed: "Hasil verifikasi beragam",
    needs_human_review: "Perlu pemeriksaan manusia",
    not_available: "Belum tersedia",
    rejected: "Tidak terverifikasi",
    verified: "Terverifikasi",
  })[String(status || "").toLowerCase()] || "Belum tersedia";
}

function normalizeParameter(parameter, fallbackSheetName = "") {
  if (!parameter || typeof parameter !== "object") return null;
  const mappingScore = Number(parameter.mapping_score ?? parameter.max_mapping_score ?? 0);
  return {
    ...parameter,
    mappingScore: Number.isFinite(mappingScore) ? Math.max(0, Math.min(1, mappingScore)) : 0,
    sourceLabel: parameterSourceLabel(parameter, fallbackSheetName),
    verificationLabel: verificationStatusLabel(parameter.verification_status),
  };
}

function sheetTone(sheet) {
  const status = String(sheet?.status || "").toLowerCase();
  if (DANGER_SHEET_STATUSES.has(status)) return "danger";
  if (WARNING_SHEET_STATUSES.has(status)) return "warning";
  const primary = sheet?.primaryParameter;
  if (
    status === "mapped"
    && primary?.mappingScore >= 0.8
    && String(primary?.verification_status || "").toLowerCase() === "verified"
  ) return "success";
  return "info";
}

function normalizeSheet(sheet = {}, index = 0) {
  const status = String(sheet.status || "no_substantive_facts").toLowerCase();
  const hidden = Boolean(sheet.hidden) || status === "hidden";
  const excluded = hidden || EXCLUDED_SHEET_STATUSES.has(status);
  const sheetName = String(sheet.sheet_name || "").trim() || "Nama sheet tidak tersedia";
  const primaryParameter = excluded ? null : normalizeParameter(sheet.primary_parameter, sheetName);
  const secondaryParameters = (excluded ? [] : list(sheet.secondary_parameters))
    .map((parameter) => normalizeParameter(parameter, sheetName))
    .filter(Boolean)
    .slice(0, MAX_SECONDARY_PARAMETERS);
  const normalized = {
    ...sheet,
    key: String(sheet.sheet_key || sheet.unit_key || `sheet-${index + 1}`),
    sheetName,
    status,
    statusLabel: sheetStatusLabel(status),
    evidenceRoleLabel: evidenceRoleLabel(sheet.evidence_role),
    coverageLabel: coverageStatusLabel(sheet.coverage_status),
    excluded,
    substantive: !excluded && SUBSTANTIVE_SHEET_STATUSES.has(status),
    primaryParameter,
    secondaryParameters,
    warnings: uniqueStrings(list(sheet.warnings)),
  };
  normalized.tone = sheetTone(normalized);
  return normalized;
}

function fallbackParameterSources(sheets) {
  const sources = new Map();
  for (const sheet of sheets) {
    for (const parameter of [sheet.primaryParameter, ...sheet.secondaryParameters].filter(Boolean)) {
      const key = parameterKey(parameter);
      if (!key) continue;
      const item = sources.get(key) || {
        ...parameter,
        source_sheets: [],
        primary_source_count: 0,
      };
      if (!item.source_sheets.some((source) => source.sheet_name === sheet.sheetName)) {
        item.source_sheets.push({
          sheet_key: sheet.key,
          sheet_name: sheet.sheetName,
          role: parameter === sheet.primaryParameter ? "primary" : "secondary",
        });
      }
      if (parameter === sheet.primaryParameter) item.primary_source_count += 1;
      sources.set(key, item);
    }
  }
  return [...sources.values()];
}

export function workbookReviewState(workbookEvidence = {}) {
  const sheets = list(workbookEvidence?.sheet_results).map(normalizeSheet);
  const statuses = sheets.map((sheet) => sheet.status);
  const showPanel = Boolean(workbookEvidence?.applicable && workbookEvidence?.is_multi_evidence);
  const hasAmbiguousSheets = statuses.some((status) => status === "ambiguous" || status === "needs_review");
  const hasBlockedSheets = statuses.some((status) => DANGER_SHEET_STATUSES.has(status));
  const hasPendingRetrieval = statuses.some((status) => status === "needs_sheet_retrieval");
  return {
    showPanel,
    hasAmbiguousSheets,
    hasBlockedSheets,
    hasPendingRetrieval,
    requiresPerSheetReview: showPanel || hasAmbiguousSheets || hasBlockedSheets || hasPendingRetrieval,
    canConfirmWorkbookAtOnce: !showPanel && !(hasAmbiguousSheets || hasBlockedSheets || hasPendingRetrieval),
    message: "Periksa dan konfirmasi setiap sheet yang relevan melalui Review Terpandu.",
  };
}

export function workbookEvidenceSummary(workbookEvidence = {}) {
  const evidence = workbookEvidence && typeof workbookEvidence === "object" ? workbookEvidence : {};
  const sheets = list(evidence.sheet_results).map(normalizeSheet);
  const summary = evidence.workbook_summary && typeof evidence.workbook_summary === "object"
    ? evidence.workbook_summary
    : {};
  const primaryParameter = normalizeParameter(summary.primary_parameter);
  const primaryKey = parameterKey(primaryParameter || {});
  const parameterSourcesRaw = list(summary.parameter_sources).length
    ? list(summary.parameter_sources)
    : fallbackParameterSources(sheets);
  const parameterSources = parameterSourcesRaw
    .map((parameter) => normalizeParameter(parameter))
    .filter(Boolean)
    .map((parameter) => ({
      ...parameter,
      workbookRole: parameterKey(parameter) === primaryKey ? "primary" : "secondary",
    }));
  const secondaryParameters = list(summary.secondary_parameters)
    .map((parameter) => normalizeParameter(parameter))
    .filter(Boolean)
    .slice(0, MAX_SECONDARY_PARAMETERS);
  const review = workbookReviewState(evidence);
  const evidenceSheets = sheets.filter((sheet) => sheet.substantive);
  const excludedSheets = sheets.filter((sheet) => !sheet.substantive);
  const warnings = uniqueStrings([
    ...list(summary.warnings),
    ...evidenceSheets
      .filter((sheet) => sheet.tone === "warning" || sheet.tone === "danger")
      .flatMap((sheet) => sheet.warnings),
  ]);
  return {
    applicable: Boolean(evidence.applicable),
    isMultiEvidence: Boolean(evidence.is_multi_evidence),
    showPanel: review.showPanel,
    status: String(evidence.status || "not_available"),
    sheetCount: Number(evidence.sheet_count ?? sheets.length) || 0,
    substantiveSheetCount: Number(evidence.substantive_sheet_count ?? evidenceSheets.length) || 0,
    parameterCount: new Set(parameterSources.map(parameterKey).filter(Boolean)).size,
    primaryParameter,
    secondaryParameters,
    parameterSources,
    evidenceSheets,
    excludedSheets,
    warnings,
    review,
  };
}

export { MAX_SECONDARY_PARAMETERS };
