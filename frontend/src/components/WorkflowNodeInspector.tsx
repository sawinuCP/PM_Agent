import { useEffect, useMemo, useState } from "react";
import {
  Brain, ChevronDown, Copy, FileInput, FileOutput, Info, ListTree, X,
} from "lucide-react";

import type { WfContextSection, WfNodeSpec } from "../api/workflowGraph";

interface Props {
  spec: WfNodeSpec;
  onClose: () => void;
  onOpenToolResult?: (resultId: string) => void;
}

type InspectorTab = "context" | "input" | "output" | "meta";

export function WorkflowNodeInspector({ spec, onClose, onOpenToolResult }: Props) {
  const hasContext = Boolean(spec.contextSections?.length);
  const hasInput = Boolean(spec.input?.trim());
  const hasOutput = Boolean(spec.output?.trim() || spec.body?.trim());
  const hasMeta = Boolean(spec.fields?.length);

  const defaultTab = useMemo((): InspectorTab => {
    if (hasContext) return "context";
    if (hasInput) return "input";
    if (hasOutput) return "output";
    return "meta";
  }, [hasContext, hasInput, hasOutput]);

  const [tab, setTab] = useState<InspectorTab>(defaultTab);
  const [openSections, setOpenSections] = useState<Set<string>>(() => new Set(["conversation"]));

  useEffect(() => {
    setTab(defaultTab);
    setOpenSections(new Set(["conversation", "bindings", "worker_prompt"]));
  }, [spec.id, defaultTab]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const tabs = [
    hasContext && { id: "context" as const, label: "Memory & prompts", icon: Brain },
    hasInput && { id: "input" as const, label: "Input", icon: FileInput },
    hasOutput && { id: "output" as const, label: "Output", icon: FileOutput },
    hasMeta && { id: "meta" as const, label: "Details", icon: ListTree },
  ].filter(Boolean) as { id: InspectorTab; label: string; icon: typeof Brain }[];

  const toggleSection = (key: string): void => {
    setOpenSections((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <>
      <div className="wf-inspector-backdrop" onClick={onClose} aria-hidden="true" />
      <div
        className="wf-inspector"
        role="dialog"
        aria-modal="true"
        aria-label={`${spec.label} workflow details`}
      >
        <header className="wf-inspector-head">
          <div className="wf-inspector-title-wrap">
            <div className="wf-inspector-title">{spec.label}</div>
            <div className="wf-inspector-kicker muted">
              {kindTitle(spec.kind)}
              {spec.concurrency ? ` · ${spec.concurrency}` : ""}
              {" · "}
              {statusTitle(spec.status)}
            </div>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm btn-icon"
            aria-label="Close inspector"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </header>

        {tabs.length > 1 && (
          <div className="wf-inspector-tabs" role="tablist">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                className={`wf-inspector-tab${tab === id ? " active" : ""}`}
                onClick={() => setTab(id)}
              >
                <Icon size={13} />
                {label}
              </button>
            ))}
          </div>
        )}

        <div className="wf-inspector-body">
          {tab === "context" && hasContext && (
            <div className="wf-inspector-sections">
              {spec.contextSections!.map((section) => (
                <ContextAccordion
                  key={section.key}
                  section={section}
                  open={openSections.has(section.key)}
                  onToggle={() => toggleSection(section.key)}
                />
              ))}
            </div>
          )}

          {tab === "input" && hasInput && (
            <Block title="Step input" content={spec.input!} format="text" />
          )}

          {tab === "output" && hasOutput && (
            <Block title="Step output" content={spec.output || spec.body || ""} format="text" />
          )}

          {tab === "meta" && hasMeta && (
            <dl className="wf-inspector-kv">
              {spec.fields!.map((field) => (
                <div key={`${field.label}-${field.value}`} className="wf-inspector-kv-row">
                  <dt>{field.label}</dt>
                  <dd>{field.value}</dd>
                </div>
              ))}
            </dl>
          )}

          {!hasContext && tab === "context" && (
            <EmptyHint
              icon={Info}
              title="No trace context recorded"
              desc="Run a new request to capture session memory, resolved entities, and prompt bindings for this step."
            />
          )}
        </div>

        {spec.resultId && onOpenToolResult && (
          <footer className="wf-inspector-foot">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => onOpenToolResult(spec.resultId!)}
            >
              Open full tool result
            </button>
          </footer>
        )}
      </div>
    </>
  );
}

function ContextAccordion({ section, open, onToggle }: {
  section: WfContextSection;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className={`wf-acc${open ? " open" : ""}`}>
      <button type="button" className="wf-acc-head" onClick={onToggle}>
        <span className="wf-acc-title">{section.title}</span>
        <ChevronDown size={14} className="wf-acc-chevron" />
      </button>
      {open && (
        <div className="wf-acc-body">
          <div className="wf-acc-actions">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => void navigator.clipboard.writeText(section.content)}
            >
              <Copy size={12} />
              Copy
            </button>
          </div>
          <pre className={`wf-inspector-pre${section.format === "json" ? " json" : ""}`}>
            {section.content}
          </pre>
        </div>
      )}
    </div>
  );
}

function Block({ title, content, format }: { title: string; content: string; format: "text" | "json" }) {
  return (
    <div className="wf-inspector-block">
      <div className="wf-inspector-block-head">
        <span>{title}</span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => void navigator.clipboard.writeText(content)}
        >
          <Copy size={12} />
          Copy
        </button>
      </div>
      <pre className={`wf-inspector-pre${format === "json" ? " json" : ""}`}>{content}</pre>
    </div>
  );
}

function EmptyHint({ icon: Icon, title, desc }: {
  icon: typeof Info;
  title: string;
  desc: string;
}) {
  return (
    <div className="wf-inspector-empty">
      <Icon size={18} />
      <div className="wf-inspector-empty-title">{title}</div>
      <div className="wf-inspector-empty-desc muted">{desc}</div>
    </div>
  );
}

function kindTitle(kind: WfNodeSpec["kind"]): string {
  if (kind === "goal") return "Goal";
  if (kind === "router") return "Router";
  if (kind === "plan") return "Planner";
  if (kind === "embed") return "Memory retrieve";
  if (kind === "answer") return "Answer";
  if (kind === "tool") return "Tool";
  return "Agent";
}

function statusTitle(status: WfNodeSpec["status"]): string {
  if (status === "done") return "Done";
  if (status === "running") return "Running";
  if (status === "failed") return "Failed";
  return "Pending";
}
