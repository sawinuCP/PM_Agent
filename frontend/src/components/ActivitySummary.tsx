import { CheckCircle2, CircleAlert, LoaderCircle, Sparkles } from "lucide-react";

import { EventType, planSteps, type RunEvent } from "../api/events";
import { deriveRunProgress } from "../api/runProgress";

interface Props {
  events: RunEvent[];
  status: "idle" | "streaming" | "completed" | "failed";
  awaitingApproval: boolean;
  awaitingClarify?: boolean;
  onShowDetails: () => void;
}

/** Plain-language progress derived from the same event stream as the inspector. */
export function ActivitySummary({ events, status, awaitingApproval, awaitingClarify, onShowDetails }: Props) {
  if (status === "idle" && events.length === 0) return null;

  const plan = [...events].reverse().find((event) => event.type === EventType.PlanCreated);
  const steps = plan && "plan" in plan ? planSteps(plan.plan.steps) : [];
  const finishedNodes = new Set(
    events.flatMap((event) =>
      event.type === EventType.NodeFinished && event.node !== "synthesis" ? [event.node] : []),
  );
  const activeNode = [...events].reverse().find((event) => event.type === EventType.NodeStarted);
  const completedSteps = finishedNodes.size;
  const totalSteps = steps.length;
  const progress = deriveRunProgress(events);

  const content = status === "failed"
    ? { icon: <CircleAlert />, title: "Something went wrong", text: progress.detail }
    : status === "completed"
      ? {
          icon: <CheckCircle2 />,
          title: "Task complete",
          text: totalSteps
            ? `${completedSteps} of ${totalSteps} planned steps completed.`
            : "Your answer is ready.",
        }
      : awaitingApproval
        ? { icon: <CircleAlert />, title: "Your approval is needed", text: "Review the request below to continue safely." }
        : awaitingClarify
          ? { icon: <CircleAlert />, title: "Needs your input", text: progress.detail || "Answer the question below to continue." }
          : {
            icon: <LoaderCircle className="spin-icon" />,
            title: completedSteps > 0 && totalSteps
              ? `${progress.title} · ${completedSteps}/${totalSteps} steps`
              : progress.title,
            text: progress.detail,
          };

  const phaseIndex = {
    start: 0,
    route: 1,
    plan: 2,
    execute: 3,
    synthesize: 4,
    write: 5,
    clarify: 1,
    failed: 0,
  }[progress.phase];

  return (
    <div className={`activity-summary ${status}`} role="status" aria-live="polite">
      <div className="activity-summary-icon">{content.icon ?? <Sparkles />}</div>
      <div className="activity-summary-copy">
        <strong>{content.title}</strong>
        <span>{content.text}</span>
        {status === "streaming" && !awaitingClarify && !awaitingApproval && (
          <div className="progress-track" aria-hidden="true">
            {[0, 1, 2, 3, 4].map((index) => (
              <span
                key={index}
                className={index < phaseIndex ? "done" : index === phaseIndex ? "current" : ""}
              />
            ))}
          </div>
        )}
        {status === "streaming" && steps.length > 0 && !awaitingClarify && !awaitingApproval && (
          <ol className="live-steps">
            {steps.map((step) => {
              const done = finishedNodes.has(step.id);
              const current = activeNode && "node" in activeNode && activeNode.node === step.id && !done;
              return (
                <li key={step.id} className={done ? "done" : current ? "current" : ""} title={step.task}>
                  <span />
                  <div className="live-step-copy">
                    <em>{step.agent?.replaceAll("-", " ") ?? step.id}</em>
                    {current && (
                      <small>{step.task.slice(0, 90)}{step.task.length > 90 ? "…" : ""}</small>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
      <button
        className="btn btn-ghost btn-sm"
        onClick={(event) => { event.stopPropagation(); onShowDetails(); }}
      >
        View activity
      </button>
    </div>
  );
}
