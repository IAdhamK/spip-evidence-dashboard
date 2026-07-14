const GRADE_ORDER = ["E", "D", "C", "B", "A"];

function uniqueBy(items, keyFor) {
  const seen = new Set();
  return items.filter((item) => {
    const key = keyFor(item);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function correctionKey(item = {}) {
  return [item.kk_id, item.kode, item.detail_kode].filter(Boolean).join("|");
}

export function correctionCatalogSelection(catalog = [], target = "") {
  const validCatalog = catalog.filter((item) => (
    item?.kk_id && item?.kode && item?.detail_kode
  ));
  const [requestedKk, requestedKode, requestedDetail] = String(target || "").split("|");
  const kkOptions = uniqueBy(validCatalog, (item) => item.kk_id);
  const activeKk = kkOptions.some((item) => item.kk_id === requestedKk)
    ? requestedKk
    : kkOptions[0]?.kk_id || "";
  const subunsurOptions = uniqueBy(
    validCatalog.filter((item) => item.kk_id === activeKk),
    (item) => item.kode,
  );
  const activeKode = subunsurOptions.some((item) => item.kode === requestedKode)
    ? requestedKode
    : subunsurOptions[0]?.kode || "";
  const parameterOptions = validCatalog.filter((item) => (
    item.kk_id === activeKk && item.kode === activeKode
  ));
  const selectedParameter = parameterOptions.find((item) => item.detail_kode === requestedDetail)
    || parameterOptions[0]
    || null;
  const grades = new Set(selectedParameter?.available_grades || []);
  return {
    kkOptions,
    subunsurOptions,
    parameterOptions,
    selectedParameter,
    activeKk,
    activeKode,
    target: correctionKey(selectedParameter),
    gradeOptions: GRADE_ORDER.filter((grade) => grades.has(grade)),
  };
}

export function correctionTargetFor(catalog = [], selection = {}) {
  const filtered = catalog.filter((item) => (
    (!selection.kkId || item.kk_id === selection.kkId)
    && (!selection.kode || item.kode === selection.kode)
    && (!selection.detailKode || item.detail_kode === selection.detailKode)
  ));
  return correctionKey(filtered[0]);
}

const STAGE_LABELS = {
  policy: "Dokumen kebijakan atau pedoman",
  socialization: "Bukti sosialisasi",
  implementation: "Bukti pelaksanaan atau implementasi",
  evaluation: "Laporan evaluasi atau pemantauan",
  improvement: "Bukti tindak lanjut atau perbaikan",
};

const SOURCE_TYPE_LABELS = {
  policy_document: "Dokumen kebijakan atau pedoman",
  socialization_record: "Bukti sosialisasi",
  implementation_record: "Bukti pelaksanaan atau implementasi",
  evaluation_report: "Laporan evaluasi atau pemantauan",
  improvement_record: "Bukti tindak lanjut atau perbaikan",
};

export function administrativeRunStatus(run = {}) {
  if (["queued", "running"].includes(run.status)) return "Sedang dianalisis";
  if (["failed", "cancelled", "blocked"].includes(run.status)) return "Analisis perlu diulang";
  if (run.status === "uploaded") return "Sudah disimpan ke evidence utama";
  if (run.status === "approved") return "Sudah disetujui";
  if (run.coverage_status !== "complete") return "Perlu melengkapi informasi";
  if (run.primary_blocked) return "Perlu diperiksa";
  return "Siap disimpan";
}

export function confidenceLabel(score = 0) {
  if (score >= 0.8) return "Tinggi";
  if (score >= 0.6) return "Sedang";
  return "Rendah";
}

const FAMILY_LABELS = {
  risk_matrix: "Matriks Peta Risiko",
  monitoring_report: "Laporan Monitoring Risiko",
  risk_policy: "Kebijakan Manajemen Risiko",
  review_audit: "Laporan Reviu atau Audit",
  transmittal_letter: "Nota Dinas atau Surat Pengantar",
  meeting_invitation: "Undangan Rapat",
  meeting_minutes: "Notulen atau Berita Acara",
  photo_documentation: "Dokumentasi Foto",
  template_form: "Formulir atau Template Kosong",
  unknown: "Jenis Dokumen Belum Dikenali",
};

const DOCUMENT_ROLE_LABELS = {
  primary: "Bukti utama",
  supporting: "Referensi pendukung",
  optional: "Bukti opsional",
  reject: "Bukan evidence mandiri",
  context: "Dokumen konteks",
  not_evidence: "Bukan evidence",
};

const NON_GRADE_FAMILIES = new Set([
  "transmittal_letter",
  "meeting_invitation",
  "photo_documentation",
  "template_form",
]);

export function documentFamilyPresentation(documentFamily = {}) {
  const family = documentFamily.family || "unknown";
  const evidenceRole = documentFamily.evidence_role || "reject";
  return {
    family,
    familyLabel: documentFamily.family_label || FAMILY_LABELS[family] || FAMILY_LABELS.unknown,
    evidenceRole,
    evidenceRoleLabel: DOCUMENT_ROLE_LABELS[evidenceRole] || "Peran perlu diperiksa",
    familyConfidence: Number(documentFamily.family_confidence || 0),
    relevantCoverage: Number(documentFamily.features?.relevant_coverage_ratio || 0),
    gradeApplicable: !NON_GRADE_FAMILIES.has(family),
    gradeStatus: documentFamily.grade_status
      || (NON_GRADE_FAMILIES.has(family) ? "not_applicable" : "blocked"),
  };
}

export function decisionConfidence(mapping = {}) {
  const score = Number(
    mapping.calibrated_decision_confidence
      ?? mapping.mapping_score
      ?? 0,
  );
  const status = mapping.decision_confidence_label;
  const label = status === "ambiguous"
    ? "Ambigu"
    : status === "needs_review"
      ? "Perlu diperiksa"
      : status === "high"
        ? "Tinggi"
        : status === "medium"
          ? "Sedang"
          : confidenceLabel(score);
  return { score, label };
}

export function gradeDirection(assessment = {}, mapping = {}) {
  const gradeStatus = assessment.grade_status || mapping.grade_status;
  if (gradeStatus === "not_applicable") {
    return {
      grade: null,
      label: "Grade tidak berlaku untuk jenis dokumen ini",
      basis: "not_applicable",
    };
  }
  if (gradeStatus === "blocked") {
    return {
      grade: null,
      label: "Belum dapat dinilai",
      basis: "blocked",
    };
  }
  if (mapping.document_role && mapping.document_role !== "primary") {
    return {
      grade: null,
      label: mapping.document_role === "supporting"
        ? "Belum dapat ditentukan dari dokumen pendukung"
        : "Belum dapat ditentukan dari dokumen ini",
      basis: "non_primary_document",
    };
  }
  if (assessment.candidate_grade) {
    const isOfficiallyAllowed = assessment.primary_allowed === true || gradeStatus === "supported";
    return {
      grade: assessment.candidate_grade,
      label: isOfficiallyAllowed
        ? `Grade ${assessment.candidate_grade}`
        : `Mendekati Grade ${assessment.candidate_grade}`,
      basis: isOfficiallyAllowed ? "supported" : "administrative_gap",
    };
  }
  return {
    grade: null,
    label: "Belum dapat dinilai",
    basis: "unavailable",
  };
}

function requirementLabel(requirement) {
  const [kind, value] = String(requirement).split(":", 2);
  if (kind === "stage") return STAGE_LABELS[value] ?? `Bukti tahap ${value}`;
  if (kind === "source_type") return SOURCE_TYPE_LABELS[value] ?? `Dokumen pendukung ${value}`;
  if (kind === "period") return "Tahun atau periode dokumen";
  if (kind === "organization") return "Nama unit kerja atau organisasi";
  if (kind === "effective_date") return "Kesesuaian tanggal berlakunya dokumen";
  if (kind === "disqualifier") return "Konfirmasi bahwa dokumen bukan sekadar rencana atau formulir kosong";
  return null;
}

export function administrativeReviewGroups({ run = {}, assessment = {}, verifications = [] } = {}) {
  const missing = [];
  const confirmations = [];
  const detected = [];
  const governance = [];
  if (run.coverage_status !== "complete") missing.push("Bagian dokumen yang belum terbaca");
  for (const requirement of assessment.missing_requirements ?? []) {
    const label = requirementLabel(requirement);
    if (!label) continue;
    if (String(requirement).startsWith("period:")) {
      confirmations.push("Konfirmasi tahun atau periode yang berlaku untuk penilaian");
    } else if (String(requirement).startsWith("organization:")) {
      confirmations.push("Konfirmasi unit kerja atau organisasi yang dinilai");
    } else if (String(requirement).startsWith("disqualifier:")) {
      confirmations.push("Konfirmasi dokumen memuat realisasi, bukan hanya rencana atau formulir kosong");
    } else {
      missing.push(label);
    }
  }
  for (const verification of verifications) {
    if (verification.period_ok === false) confirmations.push("Konfirmasi tahun atau periode yang berlaku untuk penilaian");
    if (verification.organization_ok === false) confirmations.push("Konfirmasi unit kerja atau organisasi yang dinilai");
    if (verification.source_coverage_ok === false) missing.push("Lokasi sumber yang mendukung hasil analisis");
  }
  const context = assessment.rule_trace?.context_resolution ?? {};
  if (context.period?.values?.length) {
    detected.push(`Periode ${context.period.values.join(", ")} ditemukan dalam konteks dokumen`);
  }
  if (context.organization?.values?.length) {
    detected.push(`Unit kerja ${context.organization.values.join(", ")} ditemukan dalam konteks dokumen`);
  }
  const ruleTraces = assessment.rule_trace?.rules ?? [];
  const hasDraftRule = assessment.rule_trace?.approval_status === "draft"
    || ruleTraces.some((trace) => trace.approval_status === "draft");
  if (hasDraftRule) {
    governance.push("Pedoman Grade belum disahkan; hasil tetap ditampilkan sebagai Arah Grade");
  }
  return {
    missing: [...new Set(missing)],
    confirmations: [...new Set(confirmations)],
    detected: [...new Set(detected)],
    governance: [...new Set(governance)],
  };
}

export function administrativeMissingItems(input = {}) {
  const groups = administrativeReviewGroups(input);
  return [...groups.missing, ...groups.confirmations, ...groups.governance];
}

export function primaryAdministrativeResult({ mappings = [], assessments = [], verificationResults = [] } = {}) {
  const primary = [...mappings].sort((left, right) => (
    (left.rag_rank ?? Number.MAX_SAFE_INTEGER) - (right.rag_rank ?? Number.MAX_SAFE_INTEGER)
    || (right.calibrated_decision_confidence ?? right.mapping_score ?? 0)
      - (left.calibrated_decision_confidence ?? left.mapping_score ?? 0)
  ))[0];
  if (!primary) return null;
  const assessment = assessments.find((item) => item.mapping_candidate_id === primary.id) ?? {};
  const verifications = verificationResults.filter((item) => item.mapping_candidate_id === primary.id);
  return {
    mapping: primary,
    assessment,
    verifications,
    direction: gradeDirection(assessment, primary),
  };
}
