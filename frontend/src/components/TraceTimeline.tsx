import { useMemo, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { ChevronDown, Link2 } from "lucide-react";

import { buildSpans, nestSpans, type SpanKind, type SpanNode, type TraceSpan } from "../api/spans";
import type { RunEvent } from "../api/events";
import { fmtDurationMs, fmtUsd } from "../api/format";

const KIND_LABEL: Record<SpanKind, string> = {
  run: "TRACE",
  router: "ROUTER",
  plan: "PLAN",
  agent: "AGENT",
  tool: "TOOL",
  llm: "LLM",
  embed: "EMBED",
  answer: "ANSWER",
};

interface Props {
  events: RunEvent[];
  onOpenToolResult: (resultId: string) => void;
}

export function TraceTimeline({ events, onOpenToolResult }: Props) {
  const { spans, durationMs } = useMemo(() => buildSpans(events), [events]);
  const tree = useMemo(() => nestSpans(spans), [spans]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [hover, setHover] = useState<{ left: number; labelLeft: number; ms: number } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const selected = spans.find((span) => span.id === selectedId) ?? null;

  const updatePlayhead = (event: MouseEvent<HTMLDivElement>): void => {
    const chart = chartRef.current;
    const track = chart?.querySelector(".timeline-track") as HTMLElement | null;
    if (!chart || !track) return;
    const chartBox = chart.getBoundingClientRect();
    const trackBox = track.getBoundingClientRect();
    const ratio = trackBox.width <= 0 ? 0 : (event.clientX - trackBox.left) / trackBox.width;
    const pct = Math.min(1, Math.max(0, ratio));
    const left = trackBox.left - chartBox.left + pct * trackBox.width;
    const edge = 28;
    setHover({
      left,
      labelLeft: Math.min(chartBox.width - edge, Math.max(edge, left)),
      ms: pct * durationMs,
    });
  };

  if (spans.length === 0) {
    return (
      <div className="state-block">
        <div className="state-title">No spans yet</div>
        <div className="state-desc">
          The waterfall fills in as the router, planner, agents, and tools run.
        </div>
      </div>
    );
  }

  return (
    <div className="timeline">
      <div className="timeline-legend" aria-hidden="true">
        {(Object.keys(KIND_LABEL) as SpanKind[]).map((kind) => (
          <span key={kind} className={`tl-pill tl-${kind}`}>{KIND_LABEL[kind]}</span>
        ))}
        <span className="timeline-scale muted">0 — {fmtDurationMs(durationMs)}</span>
      </div>
      <p className="timeline-hint muted">
        Hover the bars to scrub elapsed time. Overlapping tool bars with a parallel chip ran in the same wave. Sequential tools wait on an id from the previous call.
      </p>
      <div
        className="timeline-chart"
        ref={chartRef}
        onMouseMove={updatePlayhead}
        onMouseLeave={() => setHover(null)}
      >
        <div className="timeline-scrub" aria-hidden="true">
          {hover && (
            <div className="timeline-playhead-label" style={{ left: hover.labelLeft }}>
              {fmtDurationMs(hover.ms)}
            </div>
          )}
        </div>
        <div className="timeline-axis">
          {[0, 0.25, 0.5, 0.75, 1].map((pct) => (
            <span key={pct}>{fmtDurationMs(pct * durationMs)}</span>
          ))}
        </div>
        <ol className="timeline-rows" aria-label="Grouped trace waterfall">
          {tree.map((node) => (
            <TimelineBranch
              key={node.span.id}
              node={node}
              depth={0}
              durationMs={durationMs}
              selectedId={selectedId}
              collapsed={collapsed}
              onSelect={setSelectedId}
              onToggle={(id) => setCollapsed((current) => ({ ...current, [id]: !current[id] }))}
            />
          ))}
        </ol>
        {hover && (
          <div className="timeline-playhead" style={{ left: hover.left }} aria-hidden="true" />
        )}
      </div>
      {selected && (
        <div className="timeline-detail">
          <div className="timeline-detail-head">
            <span className={`tl-pill tl-${selected.kind}`}>{KIND_LABEL[selected.kind]}</span>
            <strong>{selected.name}</strong>
            <span className={`badge ${selected.status === "error" ? "badge-err" : selected.status === "running" ? "badge-warn" : "badge-ok"}`}>
              {selected.status}
            </span>
          </div>
          <dl className="kv-grid">
            <dt>Duration</dt>
            <dd>{fmtDurationMs(Math.max(0, selected.endMs - selected.startMs))}</dd>
            {selected.tokens != null && <><dt>Tokens</dt><dd>{selected.tokens.toLocaleString()}</dd></>}
            {selected.costUsd != null && <><dt>Cost</dt><dd>{fmtUsd(selected.costUsd)}</dd></>}
            {selected.node && <><dt>Node</dt><dd>{selected.node}</dd></>}
            {selected.concurrency && (
              <>
                <dt>Call mode</dt>
                <dd>{selected.concurrency === "parallel" ? "Parallel" : "Sequential"}</dd>
              </>
            )}
            {selected.detail && <><dt>Detail</dt><dd>{selected.detail}</dd></>}
          </dl>
          {selected.resultId && (
            <button className="trace-link" onClick={() => onOpenToolResult(selected.resultId!)}>
              <Link2 size={10} /> Open tool result
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function TimelineBranch({
  node, depth, durationMs, selectedId, collapsed, onSelect, onToggle,
}: {
  node: SpanNode;
  depth: number;
  durationMs: number;
  selectedId: string | null;
  collapsed: Record<string, boolean>;
  onSelect: (id: string | null) => void;
  onToggle: (id: string) => void;
}) {
  const { span, children } = node;
  const open = !collapsed[span.id];
  return (
    <li>
      <TimelineRow
        span={span}
        depth={depth}
        childCount={children.length}
        open={open}
        durationMs={durationMs}
        selected={selectedId === span.id}
        onSelect={() => onSelect(selectedId === span.id ? null : span.id)}
        onToggle={() => onToggle(span.id)}
      />
      {open && children.length > 0 && (
        <ol className="timeline-rows timeline-children">
          {children.map((child) => (
            <TimelineBranch
              key={child.span.id}
              node={child}
              depth={depth + 1}
              durationMs={durationMs}
              selectedId={selectedId}
              collapsed={collapsed}
              onSelect={onSelect}
              onToggle={onToggle}
            />
          ))}
        </ol>
      )}
    </li>
  );
}

function TimelineRow({
  span, depth, childCount, open, durationMs, selected, onSelect, onToggle,
}: {
  span: TraceSpan;
  depth: number;
  childCount: number;
  open: boolean;
  durationMs: number;
  selected: boolean;
  onSelect: () => void;
  onToggle: () => void;
}) {
  return (
    <div className={`timeline-row-wrap ${span.kind === "run" ? "root" : ""}`}>
      <div
        className={`timeline-row ${selected ? "on" : ""} ${span.status} depth-${Math.min(depth, 2)}`}
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        onClick={onSelect}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect();
          }
        }}
      >
        <span className="timeline-label">
          {childCount > 0 ? (
            <button
              type="button"
              className={`timeline-twist ${open ? "open" : ""}`}
              aria-label={open ? "Collapse group" : "Expand group"}
              aria-expanded={open}
              onClick={(event) => { event.stopPropagation(); onToggle(); }}
            >
              <ChevronDown size={12} />
            </button>
          ) : (
            <span className="timeline-twist spacer" aria-hidden="true" />
          )}
          <span className={`tl-pill tl-${span.kind}`}>{KIND_LABEL[span.kind]}</span>
          <span className="timeline-name">{span.name}</span>
          {span.concurrency && (
            <span className={`wf-chip ${span.concurrency}`}>{span.concurrency}</span>
          )}
          {childCount > 0 && <span className="timeline-kids muted">{childCount}</span>}
        </span>
        <span className="timeline-track">
          <span
            className={`timeline-bar tl-${span.kind} ${span.status}`}
            style={barStyle(span, durationMs)}
          />
        </span>
        <span className="timeline-dur mono">{fmtDurationMs(Math.max(0, span.endMs - span.startMs))}</span>
      </div>
    </div>
  );
}

function barStyle(span: TraceSpan, durationMs: number): CSSProperties {
  const width = Math.max(1.8, ((span.endMs - span.startMs) / durationMs) * 100);
  const left = (span.startMs / durationMs) * 100;
  return { left: `${left}%`, width: `${Math.min(100 - left, width)}%` };
}
