export function Notice({ text, tone = "neutral" }) {
  return <div className={`notice notice-${tone}`}>{text}</div>;
}

export function EmptyState({ text }) {
  return <div className="empty-state">{text}</div>;
}
