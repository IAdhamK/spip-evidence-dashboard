export default function OperationalReadinessPanel({ data }) {
  if (!data) return null;
  const mitigations = data.temporary_mitigations ?? [];
  const alerting = data.alerting ?? {};
  const rollout = data.rollout ?? {};
  const ruleReview = data.rule_review ?? {};
  const payloadStorage = data.deployment?.payload_storage ?? {};
  const worker = data.deployment?.worker ?? {};
  const workerReady = Boolean(worker.accepting_jobs && worker.leader_lease_active);
  const storageReady = Boolean(payloadStorage.platform_encryption_validated);
  const storageAttestation = payloadStorage.encryption_attestation ?? {};
  const shadowComparison = data.shadow_comparison ?? {};
  const checkpointing = data.checkpointing ?? {};
  const checkpointReady = Boolean(
    checkpointing.visual_ocr_batch_durable
    && checkpointing.partial_resume_checksum_bound
  );
  const uploadMetrics = data.metrics?.controlled_uploads_by_status ?? {};
  const activeUploadReservations = uploadMetrics.uploading ?? 0;
  const staleUploadReservations = data.metrics?.stale_controlled_upload_reservation_count ?? 0;
  const unresolvedUploadAmbiguities = data.metrics?.unresolved_controlled_upload_ambiguity_count ?? 0;
  const uploadControlBlocked = Boolean(
    staleUploadReservations || unresolvedUploadAmbiguities
  );

  return (
    <section className="readiness-panel" aria-label="Kesiapan operasional Document Intelligence V2">
      <div className="readiness-heading">
        <div>
          <span>Operational Readiness</span>
          <strong>{data.provider?.model || "Model belum dikonfigurasi"}</strong>
          <small>{data.provider?.name} · {data.provider?.api_surface} · alert {alerting.status || "unknown"}</small>
        </div>
        <div className={`readiness-stage ${rollout.ready ? "ready" : "blocked"}`}>
          <span>Stage efektif</span>
          <strong>{rollout.effective_stage || "development"}</strong>
          <small>Diminta: {rollout.requested_stage || "development"}</small>
        </div>
      </div>
      <div className="readiness-summary-grid">
        <div><span>Rule disahkan</span><strong>{ruleReview.approved ?? 0}/{ruleReview.total ?? 0}</strong></div>
        <div><span>Shadow gate</span><strong>{data.promotion?.shadow?.ready ? "Siap" : "Ditahan"}</strong></div>
        <div><span>Vision/OCR</span><strong>{data.ocr?.effective ?? data.vision?.effective ? "Aktif" : "Fail-closed"}</strong></div>
        <div><span>Alert aktif</span><strong>{alerting.alerts?.length ?? 0}</strong></div>
        <div className={workerReady ? "ready" : "blocked"}>
          <span>Worker V2</span>
          <strong>{workerReady ? "Menerima job" : worker.draining ? "Draining" : worker.stopping ? "Menghentikan" : "Ditahan"}</strong>
          <small>{worker.leader_lease_active ? "Leader lease aktif" : "Leader lease tidak aktif"} · {worker.queue_backend || "backend tidak diketahui"}</small>
        </div>
        <div className={checkpointReady ? "ready" : "blocked"}>
          <span>Pemulihan OCR</span>
          <strong>{checkpointReady ? "Durable per batch" : "Belum terjamin"}</strong>
          <small>{checkpointing.policy_version || "Kebijakan belum dilaporkan"} · checksum-bound resume</small>
        </div>
        <div className={uploadControlBlocked ? "blocked" : "ready"}>
          <span>Reservation upload</span>
          <strong>{uploadControlBlocked ? "Perlu rekonsiliasi" : "Aman"}</strong>
          <small>{activeUploadReservations} aktif · {staleUploadReservations} stale · {unresolvedUploadAmbiguities} ambiguity terbuka</small>
        </div>
        <div className={shadowComparison.review_target_reached ? "ready" : "blocked"}>
          <span>Shadow comparison</span>
          <strong>{shadowComparison.completed_count ?? 0}/50</strong>
          <small>{shadowComparison.review_target_reached ? "Target review tercapai" : "Belum cukup untuk keputusan passed"}</small>
        </div>
        <div className={storageReady ? "ready" : "blocked"}>
          <span>Penyimpanan dokumen</span>
          <strong>{storageReady ? "Attestation valid" : "Bukti belum valid"}</strong>
          <small>{storageReady ? `Terikat ke ${payloadStorage.configured_backend || "storage"} · berlaku sampai ${storageAttestation.expires_at || "masa berlaku terdaftar"}` : `${storageAttestation.reasons?.length ?? 0} pemeriksaan belum lulus; canary tetap ditahan`}</small>
        </div>
      </div>
      <div className="readiness-mitigation-list">
        {mitigations.map((item) => (
          <article key={item.gate} className={`readiness-mitigation ${item.status}`}>
            <div><strong>{item.gate.replaceAll("_", " ")}</strong><span>{item.status}</span></div>
            <p>{item.action}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
