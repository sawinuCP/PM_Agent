import { useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { ChevronDown, Compass, Link2 } from "lucide-react";

import { EventType, planSteps, type RunEvent } from "../api/events";
import { categoryStats, formatStepMs, stepStats, type CategoryStats } from "../api/activityStats";
import { fmtUsd } from "../api/format";
import { EVENT_META } from "./shared/eventMeta";

type Category = "all" | "plan" | "agents" | "tools" | "usage" | "embed" | "lifecycle";

const CATEGORIES: Record<Exclude<Category, "all">, string[]> = {
  plan: ["router_decision", "plan_created", "context_snapshot"],
  agents: ["agent_spawned", "node_started", "node_finished"],
  tools: ["tool_call_started", "tool_call_finished"],
  usage: ["llm_call_started", "token_usage"],
  embed: ["embedding_usage"],
  lifecycle: ["run_started", "run_finished", "run_failed", "approval_required"],
};

function formatChipMeta(stats: CategoryStats): string {
  const parts: string[] = [];
  if (stats.ms > 0) parts.push(formatStepMs(stats.ms) ?? "");
  if (stats.tokens > 0) parts.push(`${stats.tokens.toLocaleString()} tok`);
  if (stats.costUsd > 0) parts.push(fmtUsd(stats.costUsd));
  return parts.filter(Boolean).join(" · ") || "—";
}

const LABELS: { id: Category; label: string }[] = [
  { id: "all", label: "Story" },
  { id: "plan", label: "Plan" },
  { id: "agents", label: "Agents" },
  { id: "tools", label: "Tools" },
  { id: "usage", label: "Usage" },
  { id: "embed", label: "Embeddings" },
  { id: "lifecycle", label: "Lifecycle" },
];

const FRIENDLY: Record<string, string> = {
  run_started: "Started this request",
  router_decision: "Chose how to help",
  plan_created: "Built a plan",
  context_snapshot: "Injected context",
  agent_spawned: "Brought in a specialist",
  node_started: "Specialist started",
  node_finished: "Specialist finished",
  tool_call_started: "Called a tool",
  tool_call_finished: "Tool finished",
  llm_call_started: "Model call started",
  token_usage: "Model usage",
  embedding_usage: "Voyage embedding",
  approval_required: "Waiting for your approval",
  run_finished: "Finished",
  run_failed: "Stopped with an error",
};

interface AnswerGroup {
  kind: "answer";
  count: number;
  chars: number;
  seq: number;
  done: boolean;
}

type TraceItem = { kind: "event"; event: RunEvent } | AnswerGroup;

function collapseTrace(events: RunEvent[]): TraceItem[] {
  const items: TraceItem[] = [];
  let pending: RunEvent[] = [];
  const flush = (): void => {
    if (pending.length === 0) return;
    const last = pending[pending.length - 1];
    items.push({
      kind: "answer",
      count: pending.length,
      chars: pending.reduce((sum, event) => (
        event.type === EventType.AnswerDelta ? sum + event.text.length : sum
      ), 0),
      seq: pending[0].seq,
      done: last.type === EventType.AnswerDelta && last.done,
    });
    pending = [];
  };
  for (const event of events) {
    if (event.type === EventType.AnswerDelta) {
      pending.push(event);
      continue;
    }
    flush();
    items.push({ kind: "event", event });
  }
  flush();
  return items;
}

export function TracePanel({ events, onOpenToolResult }: {
  events: RunEvent[];
  onOpenToolResult: (resultId: string) => void;
}) {
  const [cat, setCat] = useState<Category>("all");
  const visibleEvents = useMemo(
    () => (cat === "all" ? events : events.filter((e) => CATEGORIES[cat].includes(e.type))),
    [events, cat],
  );
  const items = useMemo(() => collapseTrace(visibleEvents), [visibleEvents]);
  const stats = useMemo(() => ({
    all: categoryStats(events, "all"),
    plan: categoryStats(events, CATEGORIES.plan),
    agents: categoryStats(events, CATEGORIES.agents),
    tools: categoryStats(events, CATEGORIES.tools),
    usage: categoryStats(events, CATEGORIES.usage),
    embed: categoryStats(events, CATEGORIES.embed),
    lifecycle: categoryStats(events, CATEGORIES.lifecycle),
  }), [events]);

  return (
    <>
      <div className="trace-filters" role="toolbar" aria-label="Filter activity">
        {LABELS.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`chip ${cat === c.id ? "on" : ""}`}
            aria-pressed={cat === c.id}
            onClick={() => setCat(c.id)}
          >
            <span className="chip-top">
              {c.label}
              <span className="count">{(c.id === "all" ? stats.all : stats[c.id]).count}</span>
            </span>
            <span className="chip-meta">{formatChipMeta(c.id === "all" ? stats.all : stats[c.id])}</span>
          </button>
        ))}
      </div>

      <div className="panel-body">
        {items.length === 0 ? (
          <div className="state-block">
            <div className="state-title">No activity yet</div>
            <div className="state-desc">
              {cat === "all"
                ? "Steps appear here as Azend plans, uses tools, and writes the answer."
                : "Nothing in this filter for the current run."}
            </div>
          </div>
        ) : (
          <ol className="trace-list">
            {items.map((item) => (
              item.kind === "answer"
                ? <AnswerRow key={`answer-${item.seq}`} group={item} />
                : (
                  <TraceRow
                    key={item.event.seq}
                    event={item.event}
                    events={events}
                    onOpenToolResult={onOpenToolResult}
                  />
                )
            ))}
          </ol>
        )}
      </div>
    </>
  );
}

function AnswerRow({ group }: { group: AnswerGroup }) {
  const Icon = EVENT_META.answer_delta?.Icon ?? Compass;
  return (
    <li className="trace-row trace-row-answer">
      <div className="trace-icon accent"><Icon size={11} /></div>
      <div className="trace-main">
        <div className="trace-top">
          <span className="trace-type">Wrote the answer</span>
          <span className="trace-tag">{group.count} {group.count === 1 ? "chunk" : "chunks"}</span>
        </div>
        <div className="trace-sub">
          {group.done
            ? `Streamed ${group.chars.toLocaleString()} characters into the reply.`
            : `Streaming the reply… ${group.chars.toLocaleString()} characters so far.`}
        </div>
      </div>
    </li>
  );
}

function previewFor(e: RunEvent): string | null {
  if (e.type === EventType.RouterDecision) {
    return `${e.intent} path → ${e.path}${e.reason ? ` · ${e.reason}` : ""}`;
  }
  if (e.type === EventType.LlmCallStarted) {
    return e.label || "Calling model…";
  }
  if (e.type === EventType.TokenUsage) {
    const ioHint = e.input_preview || e.output_preview ? " · expand for request/response" : "";
    return `${e.prompt.toLocaleString()} in / ${e.completion.toLocaleString()} out · ${fmtUsd(e.cost_usd)}${ioHint}`;
  }
  if (e.type === EventType.EmbeddingUsage) {
    return `${e.model} · ${e.tokens.toLocaleString()} tok · ${fmtUsd(e.cost_usd)} · ${e.ms}ms${e.cached ? " · cached" : ""}`;
  }
  if (e.type === EventType.PlanCreated) {
    const steps = planSteps(e.plan.steps);
    const hint = e.hint_used ? "episodic hint used" : "no episodic hint";
    return `${steps.map((s) => s.agent).join(" → ") || "No specialist steps"} · ${hint}`;
  }
  if (e.type === EventType.ContextSnapshot) {
    const episodic = e.sections.some((s) => s.key === "episodic_hints");
    return episodic
      ? `planner · episodic memory (${e.sections.length} section${e.sections.length === 1 ? "" : "s"})`
      : `${e.node} · ${e.sections.map((s) => s.title).join(", ")}`;
  }
  if (e.type === EventType.ToolCallFinished) return e.digest_preview || e.status;
  if ("summary" in e && e.summary) return e.summary;
  if (e.type === EventType.RunFailed) return e.error;
  return null;
}

function IoPreviewBlock({ title, text }: { title: string; text: string }) {
  if (!text.trim()) return null;
  return (
    <div className="trace-io-block">
      <div className="trace-io-title">{title}</div>
      <pre className="trace-digest">{text}</pre>
    </div>
  );
}

function ExpandDetails({ event: e, onOpenToolResult }: {
  event: RunEvent;
  onOpenToolResult: (id: string) => void;
}): ReactNode {
  if (e.type === EventType.RouterDecision) {
    return (
      <dl className="kv-grid trace-kv">
        <dt>Intent</dt><dd>{e.intent}</dd>
        <dt>Path</dt><dd>{e.path}</dd>
        <dt>Reason</dt><dd>{e.reason || "—"}</dd>
        <dt>Temporal</dt><dd>{e.temporal_sensitivity ? "Yes" : "No"}</dd>
      </dl>
    );
  }
  if (e.type === EventType.LlmCallStarted) {
    return (
      <>
        <dl className="kv-grid trace-kv">
          <dt>Node</dt><dd>{e.node}</dd>
          <dt>Status</dt><dd>{e.label || "In progress until model usage event"}</dd>
        </dl>
        <IoPreviewBlock title="Input preview" text={e.input_preview ?? ""} />
      </>
    );
  }
  if (e.type === EventType.TokenUsage) {
    const total = e.prompt + e.completion;
    return (
      <>
        <dl className="kv-grid trace-kv">
          <dt>Node</dt><dd>{e.node}</dd>
          <dt>Prompt</dt><dd>{e.prompt.toLocaleString()} tokens</dd>
          <dt>Completion</dt><dd>{e.completion.toLocaleString()} tokens</dd>
          {(e.reasoning ?? 0) > 0 && (
            <><dt>Reasoning</dt><dd>{e.reasoning!.toLocaleString()} tokens</dd></>
          )}
          {(e.visible_completion ?? 0) > 0 && (
            <><dt>Visible completion</dt><dd>{e.visible_completion!.toLocaleString()} tokens</dd></>
          )}
          <dt>Total</dt><dd>{total.toLocaleString()} tokens</dd>
          <dt>Cost</dt><dd>{fmtUsd(e.cost_usd)}</dd>
          {e.model && <><dt>Model</dt><dd>{e.model}</dd></>}
          {e.reasoning_effort && <><dt>Reasoning effort</dt><dd>{e.reasoning_effort}</dd></>}
          {e.ms != null && <><dt>Latency</dt><dd>{e.ms}ms</dd></>}
        </dl>
        <IoPreviewBlock title="Input request" text={e.input_preview ?? ""} />
        <IoPreviewBlock title="Output response" text={e.output_preview ?? ""} />
      </>
    );
  }
  if (e.type === EventType.EmbeddingUsage) {
    return (
      <dl className="kv-grid trace-kv">
        <dt>Provider</dt><dd>{e.provider}</dd>
        <dt>Model</dt><dd>{e.model}</dd>
        <dt>Purpose</dt><dd>{e.node}</dd>
        <dt>Input type</dt><dd>{e.input_type || "—"}</dd>
        <dt>Tokens</dt><dd>{e.tokens.toLocaleString()}</dd>
        <dt>Cost</dt><dd>{fmtUsd(e.cost_usd)}</dd>
        <dt>Latency</dt><dd>{e.ms}ms</dd>
        <dt>Cached</dt><dd>{e.cached ? "Yes" : "No"}</dd>
      </dl>
    );
  }
  if (e.type === EventType.PlanCreated) {
    const steps = planSteps(e.plan.steps);
    return (
      <>
        <dl className="kv-grid trace-kv">
          <dt>Episodic hint</dt>
          <dd>{e.hint_used ? "Yes — similar prior run informed this plan" : "No — first time or no match"}</dd>
          <dt>Plan flag</dt>
          <dd>{e.plan.uses_episodic_hint ? "uses_episodic_hint: true" : "uses_episodic_hint: false"}</dd>
        </dl>
        {e.plan.reasoning && e.plan.reasoning.length > 0 && (
          <div className="trace-io-block">
            <div className="trace-io-title">Planner reasoning</div>
            <ol className="trace-reasoning">
              {e.plan.reasoning.map((line, index) => (
                <li key={`${index}-${line.slice(0, 24)}`}>{line}</li>
              ))}
            </ol>
          </div>
        )}
        <ol className="trace-steps">
        {steps.length === 0 && <li className="trace-expand-line">No specialist steps</li>}
        {steps.map((step, index) => (
          <li key={step.id} className="trace-step">
            <div className="trace-step-head">
              <span className="mono muted">{index + 1}.</span>
              <span className="trace-tag">{step.agent}</span>
              {step.tools.length > 0 && (
                <span className="trace-tag tool">{step.tools.join(", ")}</span>
              )}
              {step.tool_mode && step.tool_mode !== "auto" && (
                <span className={`wf-chip ${step.tool_mode}`}>{step.tool_mode}</span>
              )}
              {step.tool_mode === "auto" && (
                <span className="wf-chip serial">auto waves</span>
              )}
            </div>
            <div className="trace-expand-line">{step.task}</div>
          </li>
        ))}
        {e.plan.synthesis && (
          <li className="trace-step">
            <div className="trace-step-head">
              <span className="mono muted">Σ</span>
              <span className="trace-tag">synthesis</span>
            </div>
            <div className="trace-expand-line">{e.plan.synthesis}</div>
          </li>
        )}
      </ol>
      </>
    );
  }
  if (e.type === EventType.ContextSnapshot) {
    return (
      <div className="trace-context-stack">
        <dl className="kv-grid trace-kv">
          <dt>Node</dt><dd>{e.node}</dd>
        </dl>
        {e.sections.map((section) => (
          <div key={section.key} className="trace-context-section">
            <div className="trace-context-title">{section.title}</div>
            <pre className="trace-digest">{section.content}</pre>
          </div>
        ))}
      </div>
    );
  }
  if (e.type === EventType.ToolCallFinished) {
    return (
      <>
        <dl className="kv-grid trace-kv">
          <dt>Status</dt><dd>{e.status}</dd>
          <dt>Duration</dt><dd>{e.ms}ms</dd>
          <dt>Tool</dt><dd>{e.tool}</dd>
          <dt>Call mode</dt>
          <dd>{e.mode === "parallel" ? "Parallel with sibling tools" : e.mode === "serial" ? "Sequential (needed prior result)" : "—"}</dd>
        </dl>
        {e.digest_preview && (
          <pre className="trace-digest">{e.digest_preview}</pre>
        )}
        {e.result_id && (
          <button
            type="button"
            className="trace-link"
            onClick={(event: MouseEvent<HTMLButtonElement>) => {
              event.stopPropagation();
              onOpenToolResult(e.result_id as string);
            }}
          >
            <Link2 size={10} /> Open full tool result
          </button>
        )}
      </>
    );
  }
  return null;
}

function TraceRow({ event: e, events, onOpenToolResult }: {
  event: RunEvent;
  events: RunEvent[];
  onOpenToolResult: (id: string) => void;
}) {
  const openByDefault = e.type === EventType.ToolCallFinished || e.type === EventType.PlanCreated
    || e.type === EventType.TokenUsage;
  const [open, setOpen] = useState(openByDefault);
  const meta = EVENT_META[e.type] ?? { tone: "", Icon: Compass };
  const failedTool = e.type === "tool_call_finished" && e.status !== "ok";
  const tone = e.type === "run_failed" || failedTool ? "err"
    : meta.tone === "ok" ? "ok"
    : meta.tone === "warn" ? "warn"
    : meta.tone === "accent" ? "accent" : "";
  const expandable = e.type === "tool_call_finished" || e.type === "plan_created"
    || e.type === "context_snapshot"
    || e.type === "token_usage" || e.type === "llm_call_started"
    || e.type === "router_decision"
    || e.type === "embedding_usage";
  const preview = previewFor(e);
  const expandId = `trace-expand-${e.seq}`;
  const index = events.findIndex((item) => item.seq === e.seq && item.type === e.type);
  const metrics = stepStats(events, index < 0 ? 0 : index);

  return (
    <li className={`trace-row ${open ? "open" : ""}`}>
      <div className={`trace-icon ${tone}`}><meta.Icon size={11} /></div>
      <div className="trace-main">
        <button
          type="button"
          className="trace-heading"
          disabled={!expandable}
          aria-expanded={expandable ? open : undefined}
          aria-controls={expandable ? expandId : undefined}
          onClick={() => expandable && setOpen((value) => !value)}
        >
          <span className="trace-heading-text">
            <span className="trace-type">{FRIENDLY[e.type] ?? e.type.replaceAll("_", " ")}</span>
            {"node" in e && e.type !== EventType.ContextSnapshot && (
              <span className="trace-tag">{e.node}</span>
            )}
            {e.type === EventType.PlanCreated && e.hint_used && (
              <span className="trace-tag wf-chip parallel">episodic hint</span>
            )}
            {e.type === EventType.ContextSnapshot
              && e.sections.some((s) => s.key === "episodic_hints") && (
              <span className="trace-tag wf-chip parallel">episodic hint</span>
            )}
            {"agent" in e && <span className="trace-tag">{e.agent}</span>}
            {"tool" in e && <span className="trace-tag tool">{e.tool.replaceAll("_", " ")}</span>}
            {"mode" in e && (e.mode === "parallel" || e.mode === "serial") && (
              <span className={`wf-chip ${e.mode}`}>{e.mode === "parallel" ? "parallel" : "serial"}</span>
            )}
            {e.type === EventType.EmbeddingUsage && (
              <span className="trace-tag tool">{e.model}</span>
            )}
            {"ms" in e && typeof e.ms === "number" && e.type !== EventType.EmbeddingUsage && (
              <span className="mono muted">{e.ms}ms</span>
            )}
          </span>
          {expandable && <ChevronDown size={14} className={`trace-caret ${open ? "on" : ""}`} />}
        </button>
        <div className="trace-step-metrics">
          {formatStepMs(metrics.ms) && <span>{formatStepMs(metrics.ms)}</span>}
          {metrics.tokens != null && <span>{metrics.tokens.toLocaleString()} tok</span>}
          {metrics.costUsd != null && <span>{fmtUsd(metrics.costUsd)}</span>}
        </div>
        {preview && !open && (
          <div className={`trace-sub ${e.type === "run_failed" ? "err" : ""}`}>{preview}</div>
        )}
        {open && expandable && (
          <div id={expandId} className="trace-expand">
            <ExpandDetails event={e} onOpenToolResult={onOpenToolResult} />
          </div>
        )}
      </div>
      <span className="trace-seq">#{e.seq}</span>
    </li>
  );
}
