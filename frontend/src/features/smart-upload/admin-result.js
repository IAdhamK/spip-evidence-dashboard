const GRADE_ORDER = ["E", "D", "C", "B", "A"];

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

function missingCoreRequirements(trace = {}) {
  return (trace.missing_requirements ?? []).filter((item) => (
    !String(item).startsWith("period:")
    && !String(item).startsWith("organization:")
    && !String(item).startsWith("prerequisite_grade:")
  ));
}

export function gradeDirection(assessment = {}, mapping = {}) {
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
    const isOfficiallyAllowed = assessment.primary_allowed === true;
    return {
      grade: assessment.candidate_grade,
      label: isOfficiallyAllowed
        ? `Grade ${assessment.candidate_grade}`
        : `Mendekati Grade ${assessment.candidate_grade}`,
      basis: isOfficiallyAllowed ? "supported" : "administrative_gap",
    };
  }

  const traces = assessment.rule_trace?.rules ?? [];
  const closest = traces
    .filter((trace) => GRADE_ORDER.includes(trace.grade))
    .map((trace) => ({ trace, missing: missingCoreRequirements(trace) }))
    .sort((left, right) => (
      left.missing.length - right.missing.length
      || GRADE_ORDER.indexOf(left.trace.grade) - GRADE_ORDER.indexOf(right.trace.grade)
    ))[0];

  if (!closest) {
    return { grade: null, label: "Belum dapat diperkirakan", basis: "unavailable" };
  }
  return {
    grade: closest.trace.grade,
    label: `Mendekati Grade ${closest.trace.grade}`,
    basis: closest.missing.length ? "closest" : "administrative_gap",
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
    || (right.mapping_score ?? 0) - (left.mapping_score ?? 0)
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
