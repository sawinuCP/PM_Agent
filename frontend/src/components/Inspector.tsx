import { useMemo, useState, type ReactNode } from "react";
import { Activity, CircleAlert, GitBranch, LayoutList, Network, X } from "lucide-react";

import { terminalStatus } from "../api/client";
import { EventType, type RunEvent } from "../api/events";
import { aggregateUsage } from "../api/metrics";
import type { RunDetail } from "../api/types";
import { fmtDurationMs, fmtUsd } from "../api/format";
import type { TraceConnection } from "../hooks/useRunEvents";
import { usePanelWidth } from "../hooks/usePanelWidth";
import { ApprovalCard } from "./ui/ApprovalCard";
import { ConnectionStatus } from "./ui/ConnectionStatus";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import { StatusBadge } from "./ui/StatusBadge";
import { TracePanel } from "./TracePanel";
import { TraceTimeline } from "./TraceTimeline";
import { WorkflowGraph, type PlanShape } from "./WorkflowGraph";

type Tab = "overview" | "timeline" | "activity" | "workflow";

interface Props {
  events: RunEvent[];
  status: "idle" | "streaming" | "completed" | "failed";
  mode: "live" | "replay";
  connection?: TraceConnection;
  pendingApproval: string | null;
  pendingClarify?: string | null;
  approvalReason: string | null;
  runDetail: RunDetail | null;
  plan: PlanShape | null;
  open?: boolean;
  onClose: () => void;
  onApprove: (approved: boolean) => void;
  onOpenToolResult: (resultId: string) => void;
}

export function Inspector(p: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const { width, resizing, onResizePointerDown, onResizeKeyDown } = usePanelWidth();
  const usage = useMemo(() => aggregateUsage(p.events, p.runDetail), [p.events, p.runDetail]);
  const toolCalls = useMemo(
    () => p.events.filter((event) => event.type === EventType.ToolCallStarted).length,
    [p.events],
  );
  const router = [...p.events].reverse().find((e) => e.type === EventType.RouterDecision);
  const ended = terminalStatus(p.events);
  const displayStatus = ended
    ?? (p.runDetail?.status === "completed" || p.runDetail?.status === "failed"
      ? p.runDetail.status
      : (p.status === "streaming" ? "running" : p.status));
  const durationMs =
    p.runDetail?.started_at && p.runDetail?.finished_at
      ? new Date(p.runDetail.finished_at).getTime() - new Date(p.runDetail.started_at).getTime()
      : null;

  return (
    <aside
      className={`panel panel-inspector ${p.open !== false ? "open" : ""} ${resizing ? "resizing" : ""}`}
      aria-label="Run inspector"
      aria-hidden={p.open === false}
      style={{ width }}
    >
      <button
        type="button"
        className="inspector-resize"
        role="slider"
        aria-orientation="vertical"
        aria-label="Resize trace panel"
        aria-valuemin={440}
        aria-valuemax={typeof window === "undefined" ? 1440 : window.innerWidth - 48}
        aria-valuenow={width}
        onPointerDown={onResizePointerDown}
        onKeyDown={onResizeKeyDown}
      />
      <div className="panel-head inspector-head">
        <div className="sidebar-head-row">
          <h2 className="panel-title">Trace</h2>
          <span className={`badge ${p.mode === "live" ? "badge-warn" : "badge-info"}`}>
            {p.mode === "live" ? "live" : "replay"}
          </span>
          <ConnectionStatus connection={p.connection ?? "idle"} variant="badge" />
          <button
            className="btn btn-ghost btn-sm btn-icon"
            style={{ marginLeft: "auto" }}
            aria-label="Close activity"
            onClick={p.onClose}
          >
            <X size={14} />
          </button>
        </div>
        <div className="tabs" role="tablist" aria-label="Inspector views">
          <TabBtn id="overview" tab={tab} setTab={setTab} icon={<LayoutList size={13} />}>Overview</TabBtn>
          <TabBtn id="timeline" tab={tab} setTab={setTab} icon={<GitBranch size={13} />}>Timeline</TabBtn>
          <TabBtn id="activity" tab={tab} setTab={setTab} icon={<Activity size={13} />}>Activity</TabBtn>
          <TabBtn id="workflow" tab={tab} setTab={setTab} icon={<Network size={13} />}>Workflow</TabBtn>
        </div>
      </div>

      {p.pendingClarify && p.mode === "live" && (
        <div className="stage-approval clarify-hint inspector-hitl" role="status">
          <div className="approval-icon"><CircleAlert size={17} /></div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="approval-title">clarify_required · awaiting user input</div>
            <div className="approval-desc">{p.pendingClarify}</div>
          </div>
        </div>
      )}

      {p.pendingApproval && p.mode === "live" && (
        <ApprovalCard
          tool={p.pendingApproval}
          reason={p.approvalReason ?? undefined}
          onApprove={() => p.onApprove(true)}
          onReject={() => p.onApprove(false)}
        />
      )}

      <ErrorBoundary key={tab} variant="panel" title={`${tab} view failed`}>
      {tab === "overview" && (
        <div id="overview-panel" role="tabpanel" className="inspector-tab-panel">
          <div className="metrics">
            <div className="metric">
              <div className="metric-label">Status</div>
              <div className="metric-value">
                <StatusBadge
                  status={displayStatus}
                  pulse={p.status === "streaming" && !ended}
                />
              </div>
            </div>
            <div className="metric">
              <div className="metric-label">Tokens</div>
              <div className="metric-value">{usage.tokens.toLocaleString()}</div>
              <div className="metric-sub">{usage.prompt} in / {usage.completion} out</div>
            </div>
            <div className="metric">
              <div className="metric-label">Cost</div>
              <div className="metric-value">{fmtUsd(usage.costUsd)}</div>
              <div className="metric-sub">{usage.calls} LLM {usage.calls === 1 ? "call" : "calls"}{toolCalls ? ` · ${toolCalls} tool ${toolCalls === 1 ? "call" : "calls"}` : ""}</div>
            </div>
            <div className="metric">
              <div className="metric-label">Embeddings</div>
              <div className="metric-value">{usage.embedTokens.toLocaleString()}</div>
              <div className="metric-sub">
                {usage.embedCalls} Voyage {usage.embedCalls === 1 ? "call" : "calls"}
                {usage.embedMs ? ` · ${usage.embedMs}ms` : ""}
                {usage.embedCostUsd ? ` · ${fmtUsd(usage.embedCostUsd)}` : ""}
              </div>
            </div>
            <div className="metric">
              <div className="metric-label">Duration</div>
              <div className="metric-value">{fmtDurationMs(durationMs)}</div>
              <div className="metric-sub">{p.events.length} events</div>
            </div>
          </div>
          <div className="panel-body inspector-overview">
            <dl className="kv-grid">
              <dt>Path</dt>
              <dd>
                {router && "path" in router
                  ? `${router.intent} · ${router.path}${router.reason ? ` · ${router.reason}` : ""}`
                  : p.status === "idle" ? "Waiting for a run" : "Routing…"}
              </dd>
              <dt>Plan</dt>
              <dd>
                {p.plan?.steps?.length
                  ? p.plan.steps.map((s) => s.agent).join(" → ")
                  : "No multi-agent plan (fast or still planning)"}
              </dd>
              {p.runDetail?.goal && <><dt>Goal</dt><dd>{p.runDetail.goal}</dd></>}
              {p.runDetail?.id && <><dt>Trace id</dt><dd>{p.runDetail.id}</dd></>}
            </dl>
            <p className="inspector-hint">
              Timeline is the waterfall (one row per span). Activity is the raw event log. Workflow is the full execution graph — drag nodes to rearrange, click a node for details.
            </p>
          </div>
        </div>
      )}

      {tab === "timeline" && (
        <div id="timeline-panel" role="tabpanel" className="inspector-tab-panel">
          <div className="panel-body">
            <TraceTimeline events={p.events} onOpenToolResult={p.onOpenToolResult} />
          </div>
        </div>
      )}

      {tab === "activity" && (
        <div id="activity-panel" role="tabpanel" className="inspector-tab-panel">
          <TracePanel events={p.events} onOpenToolResult={p.onOpenToolResult} />
        </div>
      )}

      {tab === "workflow" && (
        <div id="workflow-panel" role="tabpanel" className="inspector-tab-panel">
          <WorkflowGraph
            plan={p.plan}
            events={p.events}
            runDetail={p.runDetail}
            onOpenToolResult={p.onOpenToolResult}
          />
        </div>
      )}
      </ErrorBoundary>
    </aside>
  );
}

function TabBtn({ id, tab, setTab, icon, children }: {
  id: Tab; tab: Tab; setTab: (t: Tab) => void; icon: ReactNode; children: ReactNode;
}) {
  return (
    <button
      id={`${id}-tab`}
      className={`tab ${tab === id ? "on" : ""}`}
      role="tab"
      aria-selected={tab === id}
      aria-controls={`${id}-panel`}
      tabIndex={tab === id ? 0 : -1}
      onClick={() => setTab(id)}
    >
      {icon} {children}
    </button>
  );
}
