import { CheckCircle2, CircleDashed, HelpCircle, XCircle, Zap } from "lucide-react";

const MAP: Record<string, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  completed: { label: "Completed", cls: "badge-ok", Icon: CheckCircle2 },
  ok: { label: "OK", cls: "badge-ok", Icon: CheckCircle2 },
  failed: { label: "Failed", cls: "badge-err", Icon: XCircle },
  error: { label: "Error", cls: "badge-err", Icon: XCircle },
  timeout: { label: "Timeout", cls: "badge-err", Icon: XCircle },
  running: { label: "Running", cls: "badge-warn", Icon: Zap },
  streaming: { label: "Streaming", cls: "badge-warn", Icon: Zap },
  awaiting_approval: { label: "Awaiting approval", cls: "badge-warn", Icon: HelpCircle },
  awaiting_clarification: { label: "Needs your input", cls: "badge-warn", Icon: HelpCircle },
  pending: { label: "Pending", cls: "", Icon: CircleDashed },
  idle: { label: "Idle", cls: "", Icon: CircleDashed },
};

export function StatusBadge({ status, pulse }: { status: string; pulse?: boolean }) {
  const meta = MAP[status] ?? { label: status, cls: "", Icon: CircleDashed };
  return (
    <span className={`badge ${meta.cls}`} title={status}>
      <meta.Icon size={10} className={pulse ? "pulse" : undefined} />
      {meta.label}
    </span>
  );
}
