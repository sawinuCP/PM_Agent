import { useEffect, useState } from "react";
import { Circle, Gauge, History, Moon, RefreshCw, SlidersHorizontal, Sun } from "lucide-react";

import { API_BASE, apiHeaders } from "../api/client";
import type { SessionContextStats } from "../api/types";
import type { DetailLevel } from "../hooks/useDetailLevel";
import type { Theme } from "../hooks/useTheme";
import { ContextWindowControl } from "./ContextLevelIndicator";

interface Health {
  status: string;
  database: string;
  mcp_servers: Record<string, string>;
  inference?: {
    provider: string;
    model: string;
    live: boolean;
    embeddings: string;
  };
}

interface Props {
  runContext: string | null;
  detailLevel: DetailLevel;
  historyOpen: boolean;
  telemetryOpen: boolean;
  theme: Theme;
  contextStats: SessionContextStats | null;
  contextStatsLoading: boolean;
  onToggleHistory: () => void;
  onToggleTelemetry: () => void;
  onToggleTheme: () => void;
  onDetailLevelChange: (level: DetailLevel) => void;
}

/** Floating command bar — brand, history, Simple/Technical, health. */
export function TopBar({
  runContext,
  detailLevel,
  historyOpen,
  telemetryOpen,
  theme,
  contextStats,
  contextStatsLoading,
  onToggleHistory,
  onToggleTelemetry,
  onToggleTheme,
  onDetailLevelChange,
}: Props) {
  const [health, setHealth] = useState<Health | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/health/ready`, { headers: apiHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((h: Health) => { if (!cancelled) setHealth(h); })
      .catch(() => { if (!cancelled) setHealth(null); });
    return () => { cancelled = true; };
  }, [tick]);

  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 30000);
    return () => window.clearInterval(id);
  }, []);

  const down = health === null || health.status !== "ok";
  const mcp = health?.mcp_servers ? Object.values(health.mcp_servers) : [];

  return (
    <header className="topbar">
      <div className="brand-island">
        <button
          className={`btn btn-ghost btn-sm ${historyOpen ? "on" : ""}`}
          aria-label={historyOpen ? "Hide conversations" : "Show conversations"}
          aria-pressed={historyOpen}
          onClick={onToggleHistory}
        >
          <History size={15} />
          <span className="history-label">{historyOpen ? "Hide" : "Chats"}</span>
        </button>
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <circle cx="6" cy="6" r="2.6" /><circle cx="18" cy="6" r="2.6" />
              <circle cx="12" cy="18" r="2.6" />
              <path d="M7.8 7.8 10.6 16M16.2 7.8 13.4 16M8.6 6h6.8" />
            </svg>
          </div>
          <div>
            <div className="brand-name">Azend</div>
          </div>
        </div>
      </div>

      {runContext && (
        <span className="topbar-context mono muted" title={runContext}>{runContext}</span>
      )}

      <div className="topbar-stack">
        <div className="topbar-right">
          <button
            className={`btn btn-ghost btn-sm ${telemetryOpen ? "on" : ""}`}
            aria-label={telemetryOpen ? "Hide telemetry" : "Show telemetry"}
            aria-pressed={telemetryOpen}
            onClick={onToggleTelemetry}
          >
            <Gauge size={14} />
            <span className="history-label">Telemetry</span>
          </button>
          <div className="detail-toggle" role="group" aria-label="Information detail level">
            <button
              className={detailLevel === "simple" ? "on" : ""}
              aria-pressed={detailLevel === "simple"}
              onClick={() => onDetailLevelChange("simple")}
            >
              Simple
            </button>
            <button
              className={detailLevel === "technical" ? "on" : ""}
              aria-pressed={detailLevel === "technical"}
              onClick={() => onDetailLevelChange("technical")}
            >
              <SlidersHorizontal size={12} /> Technical
            </button>
          </div>
          <button
            className="btn btn-ghost btn-sm btn-icon"
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            onClick={onToggleTheme}
          >
            {theme === "dark" ? <Sun size={13} /> : <Moon size={13} />}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            aria-label="Refresh platform health"
            onClick={() => setTick((t) => t + 1)}
          >
            <RefreshCw size={12} />
          </button>
          <span
            className={`badge ${down ? "badge-err" : mcp.every((s) => s === "up") ? "badge-ok" : "badge-warn"}`}
            title={health
              ? [
                  health.inference
                    ? `${health.inference.live ? "Live inference" : "Scripted demo"} · ${health.inference.model} · embeddings ${health.inference.embeddings}`
                    : null,
                  `db: ${health.database}`,
                  `mcp: ${Object.entries(health.mcp_servers ?? {}).map(([k, v]) => `${k}=${v}`).join(", ") || "n/a"}`,
                ].filter(Boolean).join(" · ")
              : "Platform health unavailable"}
          >
            <Circle size={9} className={down ? "" : "pulse"} fill="currentColor" />
            {health
              ? (down
                ? "Needs attention"
                : health.inference?.live
                  ? (detailLevel === "technical" ? `Live · ${health.inference.model}` : "Live")
                  : "Demo mode")
              : "Unavailable"}
          </span>
        </div>

        <div className="topbar-context-row">
          <ContextWindowControl
            stats={contextStats}
            loading={contextStatsLoading}
            variant="island"
          />
        </div>
      </div>
    </header>
  );
}
