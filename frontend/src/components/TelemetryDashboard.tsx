import { useEffect, useState, type ReactNode } from "react";
import { Activity, Clock3, Coins, Hash, TriangleAlert, X, Zap } from "lucide-react";

import { api } from "../api/client";
import type { TelemetryOut } from "../api/types";
import { fmtCount, fmtDurationMs, fmtUsd, relTime, truncate } from "../api/format";
import { StatusBadge } from "./ui/StatusBadge";

type Range = "day" | "week" | "month";

const RANGES: { id: Range; label: string; hint: string }[] = [
  { id: "day", label: "24 hours", hint: "Hourly buckets" },
  { id: "week", label: "7 days", hint: "Daily buckets" },
  { id: "month", label: "30 days", hint: "Daily buckets" },
];

interface Props {
  open: boolean;
  onClose: () => void;
  onInspectRun: (runId: string) => void;
}

export function TelemetryDashboard({ open, onClose, onInspectRun }: Props) {
  const [range, setRange] = useState<Range>("week");
  const [data, setData] = useState<TelemetryOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.telemetry(range)
      .then((report) => { if (!cancelled) setData(report); })
      .catch((err) => { if (!cancelled) setError((err as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, range]);

  const totals = data?.totals;

  return (
    <dialog
      className={`panel panel-inspector panel-telemetry ${open ? "open" : ""}`}
      open
      aria-label="Platform telemetry"
    >
      <div className="panel-head inspector-head">
        <div className="sidebar-head-row">
          <h2 className="panel-title">Telemetry</h2>
          <span className="badge badge-info">project</span>
          <button className="btn btn-ghost btn-sm btn-icon" style={{ marginLeft: "auto" }} aria-label="Close telemetry" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <p className="telemetry-lead">
          Application-wide traces, tokens, cost, and latency — the same signals LangSmith and Langfuse show on a project dashboard.
        </p>
        <div className="detail-toggle" role="group" aria-label="Time range">
          {RANGES.map((item) => (
            <button
              key={item.id}
              className={range === item.id ? "on" : ""}
              aria-pressed={range === item.id}
              title={item.hint}
              onClick={() => setRange(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="panel-body telemetry-body">
        {error && <div className="state-block"><div className="state-title">Could not load telemetry</div><div className="state-desc">{error}</div></div>}
        {!error && loading && !data && <div className="state-block"><div className="state-title">Loading…</div></div>}
        {totals && (
          <>
            <div className="metrics telemetry-kpis">
              <Kpi icon={<Hash size={13} />} label="Traces" value={fmtCount(totals.traces)} sub={`${totals.completed} ok · ${totals.failed} failed`} />
              <Kpi icon={<Zap size={13} />} label="Tokens" value={fmtCount(totals.tokens)} sub={`${fmtCount(totals.prompt_tokens)} in / ${fmtCount(totals.completion_tokens)} out`} />
              <Kpi icon={<Coins size={13} />} label="Cost" value={fmtUsd(totals.cost_usd)} sub={`${fmtCount(totals.llm_calls)} LLM calls`} />
              <Kpi icon={<Clock3 size={13} />} label="Avg latency" value={fmtDurationMs(totals.avg_latency_ms)} sub={totals.success_rate == null ? "No finished runs" : `${Math.round(totals.success_rate * 100)}% success`} />
              <Kpi icon={<TriangleAlert size={13} />} label="Errors" value={fmtCount(totals.failed)} sub={`${fmtCount(totals.running)} in flight`} />
              <Kpi icon={<Activity size={13} />} label="Success" value={totals.success_rate == null ? "—" : `${Math.round(totals.success_rate * 100)}%`} sub={`${totals.completed} of ${totals.completed + totals.failed} decided`} />
            </div>

            <section className="telemetry-section">
              <h3>Volume over time</h3>
              <TelemetryChart series={data.series} />
            </section>

            {Object.keys(data.by_status).length > 0 && (
              <section className="telemetry-section">
                <h3>Status mix</h3>
                <div className="telemetry-status">
                  {Object.entries(data.by_status).map(([status, count]) => (
                    <span key={status} className="telemetry-status-chip">
                      <StatusBadge status={status} />
                      <span className="mono">{count}</span>
                    </span>
                  ))}
                </div>
              </section>
            )}

            <section className="telemetry-section">
              <h3>Recent traces</h3>
              {data.recent.length === 0 ? (
                <div className="state-desc">No runs in this window yet. Send a chat message to create a trace.</div>
              ) : (
                <ul className="telemetry-recent">
                  {data.recent.map((row) => (
                    <li key={row.id}>
                      <button type="button" className="telemetry-recent-row" onClick={() => onInspectRun(row.id)}>
                        <StatusBadge status={row.status} />
                        <span className="telemetry-recent-goal">{truncate(row.goal, 72)}</span>
                        <span className="mono muted">{fmtCount(row.tokens)} tok</span>
                        <span className="mono muted">{fmtUsd(row.cost_usd)}</span>
                        <span className="muted">{relTime(row.started_at)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </dialog>
  );
}

function Kpi({ icon, label, value, sub }: {
  icon: ReactNode; label: string; value: string; sub: string;
}) {
  return (
    <div className="metric">
      <div className="metric-kicker">{icon}{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-sub">{sub}</div>
    </div>
  );
}

function TelemetryChart({ series }: { series: TelemetryOut["series"] }) {
  const max = Math.max(1, ...series.map((b) => b.traces));
  return (
    <div className="tl-chart" role="img" aria-label="Traces over time">
      <div className="tl-chart-bars">
        {series.map((bucket) => {
          const height = (bucket.traces / max) * 100;
          const label = bucket.bucket.includes("T")
            ? bucket.bucket.slice(11, 16)
            : bucket.bucket.slice(5);
          return (
            <div key={bucket.bucket} className="tl-chart-col" title={`${bucket.bucket}: ${bucket.traces} traces · ${fmtUsd(bucket.cost_usd)}`}>
              <div className="tl-chart-bar" style={{ height: `${Math.max(bucket.traces ? 8 : 2, height)}%` }} />
              <span>{label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
