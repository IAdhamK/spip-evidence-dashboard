from __future__ import annotations

import hashlib
from time import perf_counter

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.security import inspect_upload_security


INTAKE_ENGINE_VERSION = PIPELINE_VERSION
ROUTER_ENGINE_VERSION = PIPELINE_VERSION


class FileIntakeSecurityEngine:
    name = "file_intake_security"
    version = INTAKE_ENGINE_VERSION

    def run(
        self,
        *,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        max_bytes: int = 0,
    ) -> tuple[DocumentIdentity, list, EngineResult]:
        started = perf_counter()
        sha256 = hashlib.sha256(payload).hexdigest()
        file_kind, findings = inspect_upload_security(
            file_name,
            content_type,
            payload,
            max_bytes=max_bytes,
        )
        identity = DocumentIdentity(
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(payload),
            sha256=sha256,
            file_kind=file_kind,
        )
        blocking = [finding for finding in findings if finding.blocking]
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.BLOCKED if blocking else EngineStatus.COMPLETED,
            input_checksum=sha256,
            input_refs=[f"document:{sha256}"],
            output_refs=[f"identity:{sha256}"],
            coverage={"required": 1, "processed": 1, "failed": int(bool(blocking))},
            warnings=[finding.message for finding in findings if not finding.blocking],
            metrics={"duration_ms": duration_ms, "size_bytes": len(payload)},
            output={
                "identity": identity.to_dict(),
                "security_findings": [finding.to_dict() for finding in findings],
                "blocking_finding_count": len(blocking),
            },
            error_message=blocking[0].message if blocking else None,
        ).finish()
        return identity, findings, result


class FileRouterEngine:
    name = "file_router"
    version = ROUTER_ENGINE_VERSION

    ROUTES = {
        "pdf": "pdf_processor",
        "docx": "docx_processor",
        "xlsx": "xlsx_processor",
        "pptx": "pptx_processor",
        "image": "image_processor",
        "text": "text_processor",
    }

    def run(self, identity: DocumentIdentity, *, vision_enabled: bool) -> EngineResult:
        started = perf_counter()
        processor = self.ROUTES.get(identity.file_kind)
        supported = processor is not None
        visual_required = identity.file_kind == "image"
        warnings = []
        if visual_required and not vision_enabled:
            warnings.append("Visual/OCR Engine belum aktif; coverage visual akan tetap incomplete.")
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.COMPLETED if supported else EngineStatus.BLOCKED,
            input_checksum=identity.sha256,
            input_refs=[f"identity:{identity.sha256}"],
            output_refs=[f"route:{processor or 'unsupported'}"],
            coverage={"required": 1, "processed": int(supported), "failed": int(not supported)},
            warnings=warnings,
            metrics={"duration_ms": max(0, round((perf_counter() - started) * 1000))},
            output={
                "file_kind": identity.file_kind,
                "processor": processor,
                "supported": supported,
                "visual_required": visual_required,
                "vision_enabled": vision_enabled,
            },
            error_message=None if supported else "Processor untuk jenis file belum tersedia.",
        ).finish()
        return result
