import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, Position,
  ReactFlow, useEdgesState, useNodesState,
  type Edge, type Node, type NodeMouseHandler, type NodeProps, type ReactFlowInstance,
} from "@xyflow/react";
import { GitBranch, HelpCircle, Map as MapIcon, ScanSearch, ShieldAlert, Sparkles, Target } from "lucide-react";

import type { RunEvent } from "../api/events";
import type { RunDetail } from "../api/types";
import {
  buildWorkflowModel, workflowPositions, type PlanShape, type WfNodeSpec,
} from "../api/workflowGraph";
import { agentIcon, toolIcon } from "./shared/agentMeta";
import { WorkflowNodeInspector } from "./WorkflowNodeInspector";

export type { PlanShape };

interface Props {
  plan: PlanShape | null;
  events: RunEvent[];
  runDetail: RunDetail | null;
  onOpenToolResult?: (resultId: string) => void;
}

interface GraphNodeData extends Record<string, unknown> {
  spec: WfNodeSpec;
  onInspect: (spec: WfNodeSpec) => void;
}

type FlowNode = Node<GraphNodeData>;

const nodeTypes = { wf: GraphNode };
const CLICK_SLOP = 8;

export function WorkflowGraph({ plan, events, runDetail, onOpenToolResult }: Props) {
  const model = useMemo(
    () => buildWorkflowModel(plan, events, runDetail),
    [plan, events, runDetail],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const flowRef = useRef<ReactFlowInstance<FlowNode, Edge> | null>(null);
  const fittedRun = useRef<string>("");
  const ignorePane = useRef(false);

  const runKey = runDetail?.id ?? events[0]?.run_id ?? "draft";

  const inspectNode = useCallback((spec: WfNodeSpec) => {
    ignorePane.current = true;
    setSelectedId(spec.id);
    setNodes((current) => current.map((item) => ({ ...item, selected: item.id === spec.id })));
    window.setTimeout(() => { ignorePane.current = false; }, 0);
  }, [setNodes]);

  useEffect(() => {
    const next = toFlow(model.nodes, model.edges, inspectNode);
    setNodes((current) => {
      const posById = new Map(current.map((node) => [node.id, node.position]));
      const selected = new Set(current.filter((node) => node.selected).map((node) => node.id));
      return next.nodes.map((node) => ({
        ...node,
        position: posById.get(node.id) ?? node.position,
        selected: selected.has(node.id),
      }));
    });
    setEdges(next.edges);
  }, [model, inspectNode, setNodes, setEdges]);

  useEffect(() => {
    if (selectedId && !model.nodes.some((node) => node.id === selectedId)) {
      setSelectedId(null);
    }
  }, [model.nodes, selectedId]);

  useEffect(() => {
    if (model.nodes.length === 0) {
      fittedRun.current = "";
      return;
    }
    if (fittedRun.current === runKey) return;
    fittedRun.current = runKey;
    requestAnimationFrame(() => {
      flowRef.current?.fitView({ padding: 0.16, duration: 220, maxZoom: 1, minZoom: 0.35 });
    });
  }, [runKey, model.nodes.length]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const fit = (): void => {
      flowRef.current?.fitView({ padding: 0.16, duration: 180, maxZoom: 1, minZoom: 0.35 });
    };
    const ro = new ResizeObserver(() => requestAnimationFrame(fit));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onNodeClick: NodeMouseHandler<FlowNode> = useCallback((_event, node) => {
    inspectNode(node.data.spec);
  }, [inspectNode]);

  const onPaneClick = useCallback(() => {
    if (ignorePane.current) return;
    setSelectedId(null);
    setNodes((current) => current.map((item) => ({ ...item, selected: false })));
  }, [setNodes]);

  const resetLayout = useCallback(() => {
    const next = toFlow(model.nodes, model.edges, inspectNode);
    setNodes(next.nodes.map((node) => ({ ...node, selected: node.id === selectedId })));
    setEdges(next.edges);
    requestAnimationFrame(() => {
      flowRef.current?.fitView({ padding: 0.16, duration: 220, maxZoom: 1, minZoom: 0.35 });
    });
  }, [model, inspectNode, selectedId, setNodes, setEdges]);

  if (model.nodes.length === 0) {
    return (
      <div className="state-block" style={{ flex: 1 }}>
        <div className="state-title">Workflow</div>
        <div className="state-desc">
          The full execution graph renders here once a run starts — router, planner,
          agents, tools, and synthesis. Drag nodes to rearrange; click a node for details.
        </div>
      </div>
    );
  }

  const mode = model.parallelAgents || model.parallelTools
    ? "parallel branches"
    : "sequential";
  const selected = model.nodes.find((node) => node.id === selectedId) ?? null;

  return (
    <div className="workflow-pane">
      <div className="workflow-bar">
        <span className="workflow-path">{model.path}</span>
        <div className="workflow-actions">
          <span className="workflow-meta muted">
            {model.agentCount} {model.agentCount === 1 ? "agent" : "agents"}
            {model.toolCount ? ` · ${model.toolCount} ${model.toolCount === 1 ? "tool" : "tools"}` : ""}
            {` · ${mode}`}
            {" · drag · click a node to inspect memory & I/O"}
          </span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={resetLayout}>
            Reset layout
          </button>
        </div>
      </div>
      <div className="flow-wrap" ref={wrapRef}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onInit={(instance) => { flowRef.current = instance; }}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          fitView
          fitViewOptions={{ padding: 0.16, maxZoom: 1, minZoom: 0.35 }}
          minZoom={0.35}
          maxZoom={1.8}
          nodesDraggable
          nodesConnectable={false}
          elementsSelectable
          selectNodesOnDrag={false}
          nodeDragThreshold={CLICK_SLOP}
          panOnDrag
          zoomOnScroll
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{
            markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
          }}
        >
          <Background color="var(--border-strong)" gap={22} variant={BackgroundVariant.Dots} />
          <Controls showInteractive={false} position="bottom-right" />
        </ReactFlow>
        {selected && (
          <WorkflowNodeInspector
            spec={selected}
            onClose={onPaneClick}
            onOpenToolResult={onOpenToolResult}
          />
        )}
      </div>
    </div>
  );
}

function GraphNode({ data, selected }: NodeProps<FlowNode>) {
  const spec = data.spec;
  const origin = useRef<{ x: number; y: number } | null>(null);
  const { Icon, color } = kindIcon(spec);
  const mark = spec.status === "done" ? "✓"
    : spec.status === "running" ? "…"
      : spec.status === "failed" ? "✗" : "";
  const cls = [
    "wf-node",
    spec.kind,
    spec.status,
    selected ? "picked" : "",
    spec.inspectable ? "inspectable" : "",
    spec.resultId ? "has-result" : "",
    spec.kind === "goal" || spec.kind === "router" || spec.kind === "plan" || spec.kind === "answer"
      || spec.kind === "clarify" || spec.kind === "approval"
      ? "synthetic" : "",
  ].filter(Boolean).join(" ");

  const openIfClick = (event: { clientX: number; clientY: number }): void => {
    const start = origin.current;
    origin.current = null;
    if (!start) return;
    if (Math.abs(event.clientX - start.x) > CLICK_SLOP) return;
    if (Math.abs(event.clientY - start.y) > CLICK_SLOP) return;
    data.onInspect(spec);
  };

  return (
    <div
      className={cls}
      title={spec.inspectable
        ? "Click to inspect memory, prompts, input & output · drag to move"
        : "Drag to move"}
      onPointerDown={(event) => { origin.current = { x: event.clientX, y: event.clientY }; }}
      onPointerUp={openIfClick}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="wf-head">
        <span className="wf-agent-icon"><Icon size={12} style={{ color }} /></span>
        <span className="wf-name">{spec.label}</span>
        {spec.concurrency && (
          <span className={`wf-chip ${spec.concurrency}`}>
            {spec.concurrency === "parallel" ? "parallel" : "then"}
          </span>
        )}
        {mark && <span className="wf-status mono muted">{mark}</span>}
      </div>
      {spec.sub && <div className="wf-sub">{spec.sub}</div>}
      {spec.inspectable && (
        <button
          type="button"
          className={`wf-open nodrag nopan${spec.resultId ? " wf-open-tool" : ""}`}
          onClick={(event) => {
            event.stopPropagation();
            data.onInspect(spec);
          }}
        >
          View details
        </button>
      )}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function kindTitle(kind: WfNodeSpec["kind"]): string {
  if (kind === "goal") return "Goal";
  if (kind === "router") return "Router";
  if (kind === "plan") return "Planner";
  if (kind === "embed") return "Memory";
  if (kind === "answer") return "Synthesis";
  if (kind === "clarify") return "Human clarify";
  if (kind === "approval") return "Human approval";
  if (kind === "tool") return "Tool";
  return "Agent";
}

function kindIcon(spec: WfNodeSpec): { Icon: typeof Target; color: string } {
  if (spec.kind === "goal") return { Icon: Target, color: "var(--accent-strong)" };
  if (spec.kind === "router") return { Icon: GitBranch, color: "var(--info)" };
  if (spec.kind === "plan") return { Icon: MapIcon, color: "var(--warn)" };
  if (spec.kind === "embed") return { Icon: ScanSearch, color: "var(--text-2)" };
  if (spec.kind === "answer") return { Icon: Sparkles, color: "var(--warn)" };
  if (spec.kind === "clarify") return { Icon: HelpCircle, color: "var(--warn)" };
  if (spec.kind === "approval") return { Icon: ShieldAlert, color: "var(--danger)" };
  if (spec.kind === "tool") return toolIcon(spec.label);
  return agentIcon(spec.label);
}

function toFlow(
  specs: ReturnType<typeof buildWorkflowModel>["nodes"],
  links: ReturnType<typeof buildWorkflowModel>["edges"],
  onInspect: (spec: WfNodeSpec) => void,
): { nodes: FlowNode[]; edges: Edge[] } {
  const pos = workflowPositions(specs);
  const nodes: FlowNode[] = specs.map((spec) => ({
    id: spec.id,
    type: "wf",
    position: pos[spec.id],
    data: { spec, onInspect },
    draggable: true,
  }));
  const edges: Edge[] = links.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    animated: false,
    style: {
      stroke: edge.kind === "parallel" ? "var(--info)"
        : edge.kind === "serial" ? "rgba(62, 207, 142, 0.7)"
          : "var(--border-strong)",
      strokeWidth: 1.6,
      strokeDasharray: edge.kind === "parallel" ? "6 4" : undefined,
    },
    labelStyle: { fontSize: 10, fill: "var(--text-3)", fontWeight: 650 },
    labelBgStyle: { fill: "var(--bg)", fillOpacity: 0.9 },
    labelBgPadding: [4, 2] as [number, number],
    labelBgBorderRadius: 4,
  }));
  return { nodes, edges };
}
