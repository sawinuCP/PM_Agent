import { lazy, Suspense, useEffect, useRef, useState, type Ref } from "react";
import { ArrowUp, ChevronRight, CircleAlert, Sparkles, User } from "lucide-react";

import type { RunEvent } from "../api/events";
import { deriveRunProgress } from "../api/runProgress";
import type { DetailLevel } from "../hooks/useDetailLevel";
import { ActivitySummary } from "./ActivitySummary";
import { ApprovalCard } from "./ui/ApprovalCard";

const MessageContent = lazy(() => import("./MessageContent"));

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  ts?: string;
  error?: boolean;
  id?: string;
  runId?: string | null;
  status?: "pending" | "streaming" | "completed" | "failed";
}

interface Props {
  messages: ChatMessage[];
  historyLoading: boolean;
  submitting?: boolean;
  streaming: boolean;
  status: "idle" | "streaming" | "completed" | "failed";
  events: RunEvent[];
  /** In-flight run kept subscribed even when the inspector shows another run. */
  liveRunId?: string | null;
  liveEvents?: RunEvent[];
  liveStatus?: "idle" | "streaming" | "completed" | "failed";
  detailLevel: DetailLevel;
  pendingApproval: string | null;
  pendingClarify: string | null;
  awaitingUserInput?: boolean;
  approvalReason: string | null;
  activeRunId: string | null;
  errorBanner: string | null;
  onDismissError: () => void;
  onSend: (text: string) => void;
  onShowDetails: () => void;
  onInspectRun: (runId: string) => void;
  onApprove: (approved: boolean) => void;
}

export function ChatPane(p: Props) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const selectedRef = useRef<HTMLDivElement | null>(null);
  const lastText = p.messages.map((m) => m.text).join("\0");
  useEffect(() => {
    if (p.historyLoading) return;
    if (p.streaming || !selectedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      return;
    }
    selectedRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [p.messages.length, p.streaming, p.historyLoading, p.activeRunId, lastText]);

  return (
    <section id="conversation" className="panel panel-chat" aria-label="Conversation" tabIndex={-1}>
      {p.errorBanner && (
        <div className="error-banner" role="alert">
          <CircleAlert size={15} />
          <span className="msg">{p.errorBanner}</span>
          <button className="btn btn-ghost btn-sm" onClick={p.onDismissError}>Dismiss</button>
        </div>
      )}

      <div className="chat-scroll">
        {p.historyLoading ? (
          <>
            <div className="skeleton skeleton-bubble right" />
            <div className="skeleton skeleton-bubble left" />
            <div className="skeleton skeleton-bubble left" style={{ height: 34 }} />
          </>
        ) : p.messages.length === 0 ? (
          <Welcome simple={p.detailLevel === "simple"} onSend={p.onSend} disabled={p.streaming} />
        ) : (
          <>
          {p.messages.map((message, index) => {
            const isLive = Boolean(message.runId && message.runId === p.liveRunId);
            const isActive = Boolean(message.runId && message.runId === p.activeRunId);
            const isPendingSubmit = Boolean(
              p.submitting
              && message.role === "assistant"
              && (message.status === "pending" || message.status === "streaming")
              && !message.runId,
            );
            const isClarifyQuestion = Boolean(
              p.pendingClarify
              && message.role === "assistant"
              && message.text === p.pendingClarify
              && message.status === "completed",
            );
            const events = isLive
              ? (p.liveEvents ?? [])
              : isActive ? p.events : [];
            const runStatus = isLive
              ? (p.liveStatus ?? "streaming")
              : isActive ? p.status : isPendingSubmit ? "streaming" : "idle";
            return (
              <ChatMessageRow
                key={message.id ?? `${message.runId ?? "message"}-${message.role}-${index}`}
                msg={message}
                selected={isActive}
                rowRef={isActive && message.role === "assistant" ? selectedRef : undefined}
                events={events}
                runStatus={runStatus}
                awaitingApproval={Boolean(isActive && p.pendingApproval)}
                awaitingClarify={Boolean(isActive && p.pendingClarify && !isClarifyQuestion)}
                clarifyFallback={isActive && message.role === "assistant" ? p.pendingClarify : null}
                onShowDetails={p.onShowDetails}
                onInspectRun={p.onInspectRun}
              />
            );
          })}
          {p.streaming && p.liveRunId && !p.messages.some((m) =>
            m.role === "assistant" && (m.runId === p.liveRunId || m.status === "pending" || m.status === "streaming")) && (
            <ChatMessageRow
              msg={{
                role: "assistant",
                text: "",
                status: "streaming",
                runId: p.liveRunId,
                ts: new Date().toISOString(),
              }}
              selected
              events={p.liveEvents ?? []}
              runStatus={p.liveStatus ?? "streaming"}
              awaitingApproval={Boolean(p.pendingApproval)}
              awaitingClarify={Boolean(p.pendingClarify)}
              onShowDetails={p.onShowDetails}
              onInspectRun={p.onInspectRun}
            />
          )}
          </>
        )}
        <div ref={bottomRef} />
      </div>

      {p.detailLevel === "simple" && p.pendingClarify && (
        <div className="stage-approval clarify-hint" role="status">
          <div className="approval-icon"><CircleAlert size={17} /></div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="approval-title">Azend needs a bit more detail</div>
            <div className="approval-desc">{p.pendingClarify}</div>
          </div>
        </div>
      )}

      {p.detailLevel === "simple" && p.pendingApproval && (
        <div className="stage-approval">
          <ApprovalCard
            simple
            tool={p.pendingApproval}
            reason={p.approvalReason ?? undefined}
            onApprove={() => p.onApprove(true)}
            onReject={() => p.onApprove(false)}
          />
        </div>
      )}

      <Composer
        disabled={((p.streaming || p.submitting) && !p.awaitingUserInput) || Boolean(p.pendingApproval)}
        clarifyMode={Boolean(p.awaitingUserInput)}
        simple={p.detailLevel === "simple"}
        onSend={p.onSend}
      />
    </section>
  );
}

function ChatMessageRow({
  msg,
  selected,
  rowRef,
  events,
  runStatus,
  awaitingApproval,
  awaitingClarify,
  clarifyFallback,
  onShowDetails,
  onInspectRun,
}: {
  msg: ChatMessage;
  selected: boolean;
  rowRef?: Ref<HTMLDivElement>;
  events: RunEvent[];
  runStatus: "idle" | "streaming" | "completed" | "failed";
  awaitingApproval: boolean;
  awaitingClarify?: boolean;
  clarifyFallback?: string | null;
  onShowDetails: () => void;
  onInspectRun: (runId: string) => void;
}) {
  const inspect = () => {
    if (msg.role === "assistant" && msg.runId) onInspectRun(msg.runId);
  };
  const displayText = msg.text || clarifyFallback || "";
  const parkedClarify = Boolean(clarifyFallback && !msg.text);
  const inFlight = msg.role === "assistant"
    && (msg.status === "pending" || msg.status === "streaming")
    && !awaitingClarify
    && !parkedClarify;
  const streamingText = inFlight && Boolean(displayText);
  const canInspect = msg.role === "assistant" && Boolean(msg.runId);
  const progressDetail = inFlight ? deriveRunProgress(events).detail : "Generating response…";

  return (
    <div
      ref={rowRef}
      className={`msg-row ${msg.role}${selected ? " selected" : ""}${canInspect ? " inspectable" : ""}${inFlight ? " in-flight" : ""}`}
      role={canInspect ? "button" : undefined}
      tabIndex={canInspect ? 0 : undefined}
      aria-label={canInspect ? "View activity for this answer" : undefined}
      onClick={canInspect ? inspect : undefined}
      onKeyDown={(event) => {
        if (canInspect && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          inspect();
        }
      }}
    >
      <div className="msg-who">
        <span className="msg-avatar" aria-hidden="true">
          {msg.role === "user" ? <User size={12} /> : <Sparkles size={12} />}
        </span>
        {msg.role === "user" ? "You" : "Azend"}
      </div>
      {inFlight ? (
        <div className="msg-pending">
          <ActivitySummary
            events={events}
            status={runStatus === "failed" ? "failed" : "streaming"}
            awaitingApproval={awaitingApproval}
            awaitingClarify={awaitingClarify}
            onShowDetails={onShowDetails}
          />
          {streamingText ? (
            <div className="msg-bubble streaming msg-enter" aria-live="polite">
              <Suspense fallback={<span>{displayText}</span>}>
                <MessageContent text={displayText} />
              </Suspense>
              <span className="stream-caret" aria-hidden="true" />
            </div>
          ) : (
            <div className="typing-indicator" aria-label="Generating response">
              <span className="typing-dots">
                <span /><span /><span />
              </span>
              <span className="typing-label">{progressDetail}</span>
            </div>
          )}
        </div>
      ) : displayText ? (
        <div className={`msg-bubble${msg.error ? " error" : ""} msg-enter`}>
          {msg.role === "assistant"
            ? (
              <Suspense fallback={<span>{displayText}</span>}>
                <MessageContent text={displayText} />
              </Suspense>
            )
            : displayText}
        </div>
      ) : null}
      <div className="msg-footer">
        {msg.ts && <span className="msg-time">{new Date(msg.ts).toLocaleTimeString()}</span>}
        {canInspect && !inFlight && (
          <button
            className="message-detail-link"
            onClick={(event) => { event.stopPropagation(); inspect(); }}
          >
            View activity <ChevronRight size={11} />
          </button>
        )}
      </div>
    </div>
  );
}

const STARTERS = [
  {
    label: "Sprint tickets",
    desc: "See what is still open in the current sprint.",
    text: "What are the open Jira tickets in this sprint?",
  },
  {
    label: "Velocity",
    desc: "Forecast the next sprint from recent history.",
    text: "Predict the velocity for our next sprint based on previous Jira sprints",
  },
  {
    label: "Bug trend",
    desc: "Track defects across the last three sprints.",
    text: "Analyze the bug trend across the last three sprints",
  },
];

function Welcome({ simple, onSend, disabled }: {
  simple: boolean; onSend: (text: string) => void; disabled: boolean;
}) {
  return (
    <div className="welcome">
      <div className="welcome-kicker">{simple ? "Work assistant" : "Agent workspace"}</div>
      <h2>{simple ? "What should we look at?" : "Give the team a goal"}</h2>
      <p>
        {simple
          ? "Ask about sprint work, delivery trends, or velocity. Azend chooses specialists and brings back a grounded answer."
          : "The router plans a path, specialists do the work, and every step is available in Activity."}
      </p>
      <ul className="goal-grid">
        {STARTERS.map((item) => (
          <li key={item.label}>
            <button
              type="button"
              className="goal-card"
              disabled={disabled}
              onClick={() => onSend(item.text)}
            >
              <strong>{item.label}</strong>
              <span>{item.desc}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Composer({ disabled, clarifyMode, simple, onSend }: {
  disabled: boolean;
  clarifyMode?: boolean;
  simple: boolean;
  onSend: (t: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const areaRef = useRef<HTMLTextAreaElement | null>(null);
  const resize = (el: HTMLTextAreaElement | null): void => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 148)}px`;
  };
  const send = (): void => {
    const text = draft.trim();
    if (!text || disabled) return;
    onSend(text);
    setDraft("");
    if (areaRef.current) {
      areaRef.current.style.height = "auto";
    }
  };
  return (
    <div className={`composer${clarifyMode ? " composer-clarify" : ""}`}>
      {clarifyMode && (
        <div className="composer-clarify-label">Your answer</div>
      )}
      <div className="composer-box">
        <textarea
          ref={areaRef}
          value={draft}
          rows={1}
          aria-label={clarifyMode
            ? "Answer Azend's question"
            : simple ? "Message Azend" : "Message the agent team"}
          placeholder={clarifyMode
            ? "Type your answer here…"
            : simple ? "Ask about a sprint, tickets, or delivery…" : "Describe a goal for the agent team…"}
          disabled={disabled}
          onChange={(e) => {
            setDraft(e.target.value);
            resize(e.currentTarget);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        <button
          className="btn btn-primary btn-icon"
          disabled={disabled || !draft.trim()}
          onClick={send}
          aria-label="Send message"
        >
          <ArrowUp size={16} />
        </button>
      </div>
      <div className="composer-hint">
        <span><kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> newline</span>
        {disabled && !clarifyMode && (
          <span className="composer-working">
            <span className="typing-dots" aria-hidden="true"><span /><span /><span /></span>
            {simple ? "Working on it…" : "Streaming trace…"}
          </span>
        )}
      </div>
    </div>
  );
}
