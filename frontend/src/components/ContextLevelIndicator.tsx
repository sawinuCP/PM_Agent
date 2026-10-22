import { useCallback, useEffect, useRef, useState } from "react";
import { Gauge, Layers } from "lucide-react";

import type { SessionContextStats } from "../api/types";

interface Props {
  stats: SessionContextStats | null;
  loading?: boolean;
  visible?: boolean;
  variant?: "icon" | "island";
}

function contextLevel(stats: SessionContextStats | null): "ok" | "warn" | "critical" {
  if (!stats) return "ok";
  const fillPct = Math.min(100, stats.utilization_pct);
  const thresholdPct = Math.min(100, stats.threshold_pct * 100);
  if (fillPct >= thresholdPct) return "critical";
  if (fillPct >= thresholdPct * 0.75) return "warn";
  return "ok";
}

/** Top-bar icon; hover or click opens a right-aligned context details popover. */
export function ContextWindowControl({
  stats,
  loading,
  visible = true,
  variant = "icon",
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<number | null>(null);

  const clearCloseTimer = useCallback(() => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const scheduleClose = useCallback(() => {
    clearCloseTimer();
    closeTimer.current = window.setTimeout(() => setOpen(false), 180);
  }, [clearCloseTimer]);

  const show = useCallback(() => {
    clearCloseTimer();
    setOpen(true);
  }, [clearCloseTimer]);

  useEffect(() => () => clearCloseTimer(), [clearCloseTimer]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!visible) return null;

  const fillPct = stats ? Math.min(100, stats.utilization_pct) : 0;
  const level = contextLevel(stats);
  const islandClass = variant === "island" ? " context-window-control-island" : "";

  const trigger = (
    <button
      type="button"
      className={`btn btn-ghost btn-sm btn-icon context-window-trigger${open ? " on" : ""}`}
      aria-label="Session context window"
      aria-expanded={open}
      aria-haspopup="dialog"
      onClick={() => setOpen((value) => !value)}
    >
      <Layers size={14} />
      {stats && !loading && (
        <span className={`context-window-pip context-window-pip-${level}`} aria-hidden="true" />
      )}
    </button>
  );

  return (
    <div
      ref={rootRef}
      className={`context-window-control${islandClass}${open ? " open" : ""}`}
      onMouseEnter={show}
      onMouseLeave={scheduleClose}
    >
      {variant === "island" ? (
        <div className="context-window-island">
          <span className="context-window-island-label">Context</span>
          {stats && !loading ? (
            <span className={`context-window-island-pct context-window-island-pct-${level}`}>
              {fillPct.toFixed(1)}%
            </span>
          ) : loading ? (
            <span className="context-window-island-pct skeleton" aria-hidden="true" />
          ) : null}
          {trigger}
        </div>
      ) : (
        trigger
      )}

      {open && (
        <div
          className="context-window-popover"
          role="dialog"
          aria-label="Context window details"
          onMouseEnter={show}
          onMouseLeave={scheduleClose}
        >
          <ContextLevelPanel stats={stats} loading={loading} />
        </div>
      )}
    </div>
  );
}

function ContextLevelPanel({ stats, loading }: Pick<Props, "stats" | "loading">) {
  if (loading) {
    return (
      <div className="context-level context-level-popover context-level-loading" aria-busy="true">
        <div className="context-level-head">
          <span className="context-level-icon" aria-hidden="true"><Gauge size={14} /></span>
          <span className="context-level-title">Context window</span>
        </div>
        <div className="context-level-bar skeleton" />
        <div className="context-level-meta skeleton" style={{ height: 12, width: "55%" }} />
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="context-level context-level-popover">
        <div className="context-level-head">
          <span className="context-level-icon" aria-hidden="true"><Gauge size={14} /></span>
          <div className="context-level-copy">
            <span className="context-level-title">Context window</span>
            <span className="context-level-sub">Start a conversation to track context usage.</span>
          </div>
        </div>
      </div>
    );
  }

  const fillPct = Math.min(100, stats.utilization_pct);
  const thresholdPct = Math.min(100, stats.threshold_pct * 100);
  const level = fillPct >= thresholdPct ? "critical"
    : fillPct >= thresholdPct * 0.75 ? "warn"
      : "ok";

  return (
    <div
      className={`context-level context-level-popover context-level-${level}`}
      role="status"
      aria-label={`Session context ${formatTokens(stats.effective_tokens)} of ${formatTokens(stats.budget_tokens)} tokens`}
    >
      <div className="context-level-head">
        <span className="context-level-icon" aria-hidden="true"><Gauge size={14} /></span>
        <div className="context-level-copy">
          <span className="context-level-title">Context window</span>
          <span className="context-level-sub">
            {stats.has_summary ? "Rolling summary active · " : ""}
            Summarization at {Math.round(thresholdPct)}%
          </span>
        </div>
        <div className="context-level-numbers">
          <strong>{formatTokens(stats.effective_tokens)}</strong>
          <span className="context-level-sep">/</span>
          <span>{formatTokens(stats.budget_tokens)}</span>
          <span className="context-level-pct">{fillPct.toFixed(1)}%</span>
        </div>
      </div>

      <div className="context-level-track" aria-hidden="true">
        <div className="context-level-fill" style={{ width: `${fillPct}%` }} />
        <div
          className="context-level-threshold"
          style={{ left: `${thresholdPct}%` }}
          title={`Summarization threshold (${formatTokens(stats.threshold_tokens)})`}
        />
      </div>

      <div className="context-level-meta">
        <span className="context-level-chip">
          <Layers size={11} aria-hidden="true" />
          In prompt: {formatTokens(stats.transcript_tokens)}
        </span>
        <span className="context-level-chip">
          Session stored: {formatTokens(stats.stored_message_tokens)}
          {stats.message_count > 0 ? ` · ${stats.message_count} msgs` : ""}
        </span>
        {stats.summarization_pending && (
          <span className="context-level-chip context-level-chip-warn">Summary due</span>
        )}
      </div>
    </div>
  );
}

function formatTokens(value: number | null | undefined): string {
  const n = value ?? 0;
  if (n >= 1_000_000) {
    const scaled = n / 1_000_000;
    return `${scaled >= 10 ? scaled.toFixed(0) : scaled.toFixed(1)}M`;
  }
  if (n >= 10_000) return `${Math.round(n / 1_000)}K`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}
