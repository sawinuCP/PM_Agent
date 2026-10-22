import { LoaderCircle, Radio, RefreshCw } from "lucide-react";

import type { TraceConnection } from "../../hooks/useRunEvents";

interface Props {
  connection: TraceConnection;
  /** Compact badge for inspector header; banner for chat stage. */
  variant?: "badge" | "banner";
}

/** Shows when live SSE falls back to trace polling or replay mode is active. */
export function ConnectionStatus({ connection, variant = "banner" }: Props) {
  if (connection === "idle" || connection === "sse") return null;

  if (variant === "badge") {
    const label = connection === "polling" ? "syncing" : "replay";
    const Icon = connection === "polling" ? RefreshCw : Radio;
    return (
      <span
        className={`badge connection-badge connection-${connection}`}
        title={connection === "polling"
          ? "Live stream paused — loading updates from persisted trace"
          : "Replaying trace from storage"}
      >
        <Icon size={9} className={connection === "polling" ? "spin-icon" : undefined} />
        {label}
      </span>
    );
  }

  return (
    <div className="connection-banner" role="status" aria-live="polite">
      <LoaderCircle size={14} className="spin-icon" />
      <span>
        {connection === "polling"
          ? "Reconnecting — syncing trace updates while the run continues…"
          : "Replaying trace from storage…"}
      </span>
    </div>
  );
}
