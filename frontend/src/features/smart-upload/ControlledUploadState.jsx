const OUTCOME_LABELS = {
  confirmed_uploaded: "File terkonfirmasi ada di tujuan",
  confirmed_not_uploaded: "File terkonfirmasi tidak ada di tujuan",
};

export default function ControlledUploadState({
  action,
  reconciliation,
  busy,
  onRefresh,
  onReconcile,
}) {
  if (!action) return null;
  const resolved = Boolean(reconciliation?.effective);
  const label = resolved
    ? OUTCOME_LABELS[reconciliation.outcome]
    : action.status === "uploading"
      ? "Sedang diproses"
      : action.status === "uploaded_primary"
        ? "Sudah diupload"
        : action.status === "blocked_ambiguous"
          ? "Perlu rekonsiliasi dua reviewer"
          : action.status;
  const className = resolved ? "resolved" : reconciliation?.status || action.status;

  return (
    <div className={`controlled-upload-state ${className}`}>
      <div><strong>{label}</strong><span>Action #{action.id}</span></div>
      <p>{action.status === "blocked_ambiguous"
        ? "Jangan upload ulang. Periksa folder WebDAV dan legacy review, lalu minta dua reviewer berbeda mencatat hasil yang sama."
        : action.message}</p>
      <small>
        Legacy review {action.legacy_review_id ? `#${action.legacy_review_id}` : "belum tersedia"}
        {reconciliation ? ` · kecocokan ${reconciliation.matching_reviewer_count}/${reconciliation.required_reviewers}` : ""}
      </small>
      {action.status === "blocked_ambiguous" && !resolved ? (
        <button className="row-action-button" type="button" disabled={busy} onClick={() => onReconcile(action)}>
          Kirim rekonsiliasi
        </button>
      ) : null}
      {action.status === "uploading" ? (
        <button className="row-action-button" type="button" disabled={busy} onClick={onRefresh}>
          Segarkan status
        </button>
      ) : null}
    </div>
  );
}
