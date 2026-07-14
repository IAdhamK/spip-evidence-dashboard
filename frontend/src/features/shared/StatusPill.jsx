import {
  AlertCircle,
  ArrowUpDown,
  CheckCircle2,
  Info,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

const STATUS_ICONS = {
  Kosong: AlertCircle,
  "Terisi Sebagian": ArrowUpDown,
  Terisi: CheckCircle2,
  "Perlu Kurasi": TriangleAlert,
  Final: ShieldCheck,
};

function slug(value) {
  return String(value || "").toLowerCase().replaceAll(" ", "-");
}

export function Tooltip({ text }) {
  if (!text) return null;
  return (
    <span className="tooltip" tabIndex="0" aria-label={text}>
      <Info size={14} />
      <span className="tooltip-bubble">{text}</span>
    </span>
  );
}

export function StatusPill({ status, explanation }) {
  const Icon = STATUS_ICONS[status] ?? Info;
  return (
    <span className={`status-pill status-${slug(status)}`}>
      <Icon size={15} />
      {status}
      <Tooltip text={explanation} />
    </span>
  );
}
