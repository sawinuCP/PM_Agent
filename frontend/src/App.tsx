import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Route, Routes, useNavigate, useParams } from "react-router-dom";

import { ChatPane, type ChatMessage } from "./components/ChatPane";
import { Inspector } from "./components/Inspector";
import { Sidebar } from "./components/Sidebar";
import { TelemetryDashboard } from "./components/TelemetryDashboard";
import { ToolResultDrawer } from "./components/ToolResultDrawer";
import { TopBar } from "./components/TopBar";
import { api } from "./api/client";
import { EventType } from "./api/events";
import { useRunEvents } from "./hooks/useRunEvents";
import { useDetailLevel } from "./hooks/useDetailLevel";
import { useTheme } from "./hooks/useTheme";
import { ToastProvider, useToasts } from "./components/ui/Toasts";
import type { MessageOut, RunDetail, RunSummary, SessionContextStats, SessionOut } from "./api/types";
import type { PlanShape } from "./components/WorkflowGraph";
import { ROUTES } from "./routes";

function findLastIndex<T>(items: T[], pred: (item: T) => boolean): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (pred(items[index])) return index;
  }
  return -1;
}

function Workspace() {
  const { push } = useToasts();
  const { detailLevel, setDetailLevel } = useDetailLevel();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>();
  const activeSessionId = routeSessionId ?? null;

  const [sessions, setSessions] = useState<SessionOut[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [inspectedRunId, setInspectedRunId] = useState<string | null>(null);
  const [liveRunId, setLiveRunId] = useState<string | null>(null);
  const [liveRunGeneration, setLiveRunGeneration] = useState(0);
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [contextStats, setContextStats] = useState<SessionContextStats | null>(null);
  const [contextStatsLoading, setContextStatsLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [telemetryOpen, setTelemetryOpen] = useState(false);
  const [drawer, setDrawer] = useState<{ runId: string; resultId: string } | null>(null);
  const [clarifyResuming, setClarifyResuming] = useState(false);

  const failedBubbleFor = useRef<string | null>(null);
  const completedBubbleFor = useRef<string | null>(null);
  const sessionSelection = useRef(0);
  const hasUserInteracted = useRef(false);
  const sendSessionId = useRef<string | null>(null);
  const focusRunOnLoad = useRef<string | undefined>(undefined);
  const clarifyResumeSinceSeq = useRef(0);

  const resetDraftView = useCallback(() => {
    sessionSelection.current += 1;
    setMessages([]);
    setRuns([]);
    setContextStats(null);
    setContextStatsLoading(false);
    setInspectedRunId(null);
    setLiveRunId(null);
    setLiveRunGeneration(0);
    setRunDetail(null);
    setDrawer(null);
    setErrorBanner(null);
    setHistoryLoading(false);
    setClarifyResuming(false);
    failedBubbleFor.current = null;
    completedBubbleFor.current = null;
    sendSessionId.current = null;
    clarifyResumeSinceSeq.current = 0;
  }, []);

  // Live SSE is pinned to the in-flight send so inspecting another run never
  // tears down the subscription that delivers the assistant answer.
  const liveTrace = useRunEvents(liveRunId, "live", liveRunGeneration);
  const inspectTarget = inspectedRunId ?? null;
  const inspectTrace = useRunEvents(
    inspectTarget && inspectTarget !== liveRunId ? inspectTarget : null,
    "replay",
  );
  const activeTrace = inspectTarget && inspectTarget !== liveRunId
    ? inspectTrace
    : liveTrace;
  const activeRunId = inspectedRunId ?? liveRunId;
  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? null;

  const inspectorOpen = detailLevel === "technical";
  const [sheetMounted, setSheetMounted] = useState(inspectorOpen);
  const [sheetOpen, setSheetOpen] = useState(inspectorOpen);

  useEffect(() => {
    if (inspectorOpen) {
      setSheetMounted(true);
      const id = window.requestAnimationFrame(() => setSheetOpen(true));
      return () => window.cancelAnimationFrame(id);
    }
    setSheetOpen(false);
    const timeout = window.setTimeout(() => setSheetMounted(false), 320);
    return () => window.clearTimeout(timeout);
  }, [inspectorOpen]);

  const closeTopOverlay = useCallback(() => {
    if (telemetryOpen) {
      setTelemetryOpen(false);
      return;
    }
    if (detailLevel === "technical") {
      setDetailLevel("simple");
      return;
    }
    if (sidebarOpen) setSidebarOpen(false);
  }, [detailLevel, setDetailLevel, sidebarOpen, telemetryOpen]);

  const closeAllOverlays = useCallback(() => {
    setTelemetryOpen(false);
    if (detailLevel === "technical") setDetailLevel("simple");
    setSidebarOpen(false);
  }, [detailLevel, setDetailLevel]);

  useEffect(() => {
    if (!sidebarOpen && !inspectorOpen && !telemetryOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeTopOverlay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen, inspectorOpen, telemetryOpen, closeTopOverlay]);

  useEffect(() => {
    api.listSessions()
      .then(setSessions)
      .catch(() => {
        setSessions([]);
        setErrorBanner("Cannot reach the platform API. Is the backend running on :8000?");
      });
  }, []);

  const hydrateMessages = useCallback((rows: MessageOut[]): ChatMessage[] =>
    rows.map((m) => ({
      id: m.id,
      role: m.role,
      text: m.content.text ?? "",
      ts: m.created_at,
      runId: m.run_id,
      status: "completed",
    })), []);

  const autoRuns = useCallback(async (sessionId: string): Promise<{ runs: RunSummary[] }> => {
    try {
      return await api.listRuns(sessionId);
    } catch {
      return { runs: [] };
    }
  }, []);

  const mergeServerMessages = useCallback((
    server: ChatMessage[],
    local: ChatMessage[],
  ): ChatMessage[] => {
    const serverRunIds = new Set(server.map((m) => m.runId).filter(Boolean));
    const serverHasAssistant = new Set(
      server.filter((m) => m.role === "assistant" && m.runId && m.text)
        .map((m) => m.runId as string),
    );
    const extras = local.filter((m) => {
      if (!m.runId) return m.status === "pending" || m.status === "streaming";
      if (!serverRunIds.has(m.runId)) return true;
      if (m.role === "assistant" && m.text && !serverHasAssistant.has(m.runId)) return true;
      return false;
    });
    if (extras.length === 0) return server;
    const seen = new Set(server.map((m) => `${m.role}:${m.runId ?? m.id}`));
    return [
      ...server,
      ...extras.filter((m) => !seen.has(`${m.role}:${m.runId ?? m.id}`)),
    ];
  }, []);

  const refreshContextStats = useCallback(async (sessionId: string) => {
    const selection = sessionSelection.current;
    try {
      const stats = await api.sessionContextStats(sessionId);
      if (selection !== sessionSelection.current) return;
      if (sendSessionId.current && sendSessionId.current !== sessionId) return;
      setContextStats(stats);
    } catch {
      /* keep last known stats */
    } finally {
      if (selection === sessionSelection.current) setContextStatsLoading(false);
    }
  }, []);

  const refreshSessionMessages = useCallback(async (sessionId: string) => {
    const selection = sessionSelection.current;
    try {
      const msgResp = await api.listMessages(sessionId);
      if (selection !== sessionSelection.current) return;
      if (sendSessionId.current && sendSessionId.current !== sessionId) return;
      setMessages((prev) => mergeServerMessages(hydrateMessages(msgResp.messages), prev));
    } catch {
      /* keep optimistic UI */
    }
    void refreshContextStats(sessionId);
  }, [hydrateMessages, mergeServerMessages, refreshContextStats]);

  const loadSessionFromRoute = useCallback(async (
    id: string,
    focusRunId?: string,
  ) => {
    const selection = ++sessionSelection.current;
    setSidebarOpen(false);
    setDrawer(null);
    setRunDetail(null);
    setErrorBanner(null);
    if (sendSessionId.current !== id) setLiveRunId(null);
    setHistoryLoading(true);
    setContextStatsLoading(true);
    setContextStats(null);
    const [msgResp, runList, statsResp, sessionRecord] = await Promise.all([
      api.listMessages(id).catch(() => ({ messages: [] as MessageOut[] })),
      autoRuns(id),
      api.sessionContextStats(id).catch(() => null),
      api.getSession(id).catch(() => null),
    ]);
    if (selection !== sessionSelection.current) return;
    if (sessionRecord === null) {
      push("error", "Conversation not found.");
      resetDraftView();
      navigate(ROUTES.newChat, { replace: true });
      return;
    }
    setMessages(hydrateMessages(msgResp.messages));
    setRuns(runList.runs);
    setContextStats(statsResp);
    setContextStatsLoading(false);
    const parkedClarify = runList.runs.find((run) => run.status === "awaiting_clarification");
    if (parkedClarify) {
      setInspectedRunId(parkedClarify.id);
      setLiveRunId(parkedClarify.id);
      setLiveRunGeneration((gen) => gen + 1);
      sendSessionId.current = id;
    } else {
      setInspectedRunId(focusRunId ?? runList.runs[0]?.id ?? null);
      if (sendSessionId.current !== id) {
        setLiveRunId(null);
        sendSessionId.current = null;
      }
    }
    failedBubbleFor.current = null;
    completedBubbleFor.current = null;
    setHistoryLoading(false);
  }, [autoRuns, hydrateMessages, navigate, push, resetDraftView]);

  useEffect(() => {
    if (!routeSessionId) {
      // Draft home view — keep in-flight sends intact until navigation to /chat/:id.
      if (!submitting) resetDraftView();
      return;
    }
    if (sendSessionId.current === routeSessionId && messages.length > 0) {
      void refreshContextStats(routeSessionId);
      void autoRuns(routeSessionId).then((r) => setRuns(r.runs));
      return;
    }
    const focusRunId = focusRunOnLoad.current;
    focusRunOnLoad.current = undefined;
    void loadSessionFromRoute(routeSessionId, focusRunId);
    // Route changes only — avoid reloading while the same chat is streaming.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSessionId]);

  const selectSession = useCallback((
    id: string,
    focusRunId?: string,
  ) => {
    hasUserInteracted.current = true;
    focusRunOnLoad.current = focusRunId;
    setSidebarOpen(false);
    navigate(ROUTES.chat(id));
  }, [navigate]);

  const inspectRun = useCallback((runId: string) => {
    setInspectedRunId(runId);
    setDrawer(null);
    setTelemetryOpen(false);
    setDetailLevel("technical");
    void api.runDetail(runId)
      .then(setRunDetail)
      .catch(() => setRunDetail(null));
  }, [setDetailLevel]);

  const openHistoricalTrace = useCallback(async (runId: string) => {
    hasUserInteracted.current = true;
    setTelemetryOpen(false);
    setDrawer(null);
    setDetailLevel("technical");
    try {
      const detail = await api.runDetail(runId);
      if (detail.session_id && detail.session_id !== activeSessionId) {
        selectSession(detail.session_id, runId);
        setRunDetail(detail);
        return;
      }
      setRunDetail(detail);
      setInspectedRunId(runId);
    } catch (error) {
      setInspectedRunId(runId);
      push("error", (error as Error).message);
    }
  }, [activeSessionId, push, selectSession, setDetailLevel]);

  const newSession = useCallback(() => {
    hasUserInteracted.current = true;
    setSidebarOpen(false);
    resetDraftView();
    navigate(ROUTES.newChat);
  }, [navigate, resetDraftView]);

  const deleteSession = useCallback(async (id: string) => {
    const prompt = detailLevel === "simple"
      ? "Delete this conversation? This cannot be undone."
      : "Delete this session? Derived memories stay traceable; the chat history is removed.";
    if (!window.confirm(prompt)) return;
    try {
      await api.deleteSession(id);
      const next = sessions.filter((s) => s.id !== id);
      setSessions(next);
      if (id === activeSessionId) {
        if (next[0]) navigate(ROUTES.chat(next[0].id));
        else navigate(ROUTES.newChat);
      }
      push("info", "Session deleted");
    } catch (e) {
      push("error", (e as Error).message);
    }
  }, [activeSessionId, detailLevel, navigate, push, sessions]);

  const upsertAssistant = useCallback((
    runId: string,
    text: string,
    ts: string,
    status: "streaming" | "completed",
  ) => {
    if (!text && status !== "completed") return;
    if (status === "completed") completedBubbleFor.current = runId;
    setMessages((prev) => {
      const existing = findLastIndex(prev, (m) => m.role === "assistant" && m.runId === runId);
      if (existing >= 0) {
        const current = prev[existing];
        if (current.status === "completed" && current.text && status === "streaming") return prev;
        if (current.status === "completed" && current.text === text) return prev;
        const inFlightEmpty = (current.status === "pending" || current.status === "streaming")
          && !current.text;
        if (status === "completed" && inFlightEmpty) {
          const hasUserAfter = prev.slice(existing + 1).some((m) => m.role === "user");
          if (hasUserAfter) {
            // Clarify resume: keep the new in-flight bubble; update an earlier turn if needed.
            const prior = findLastIndex(
              prev.slice(0, existing),
              (m) => m.role === "assistant" && m.runId === runId,
            );
            if (prior >= 0) {
              return prev.map((message, index) => index === prior
                ? { ...message, text, ts, status, error: false }
                : message);
            }
            return prev;
          }
          // First clarify park: fill the empty streaming bubble with the question.
          return prev.map((message, index) => index === existing
            ? { ...message, text, ts, status: "completed" as const, error: false }
            : message);
        }
        const nextText = text || current.text;
        if (!nextText) return prev;
        return prev.map((message, index) => {
          if (index === existing) {
            return { ...message, text: nextText, ts, status, error: false };
          }
          if (message.runId === runId && message.role === "user" && status === "completed") {
            return { ...message, status: "completed" as const };
          }
          return message;
        });
      }
      const orphan = findLastIndex(prev, (m) =>
        m.role === "assistant"
        && (m.runId === runId || !m.runId)
        && (m.status === "pending" || m.status === "streaming"));
      if (orphan >= 0) {
        const nextText = text || prev[orphan].text;
        if (!nextText && status !== "completed") return prev;
        return prev.map((message, index) =>
          index === orphan
            ? { ...message, runId, text: nextText, ts, status, error: false }
            : message);
      }
      if (!prev.some((m) => m.runId === runId)) return prev;
      if (!text) return prev;
      return [...prev, {
        id: `assistant-${runId}`,
        role: "assistant",
        text,
        ts,
        runId,
        status,
      }];
    });
  }, []);

  const send = useCallback(async (text: string) => {
    hasUserInteracted.current = true;
    const wasAwaitingClarify = Boolean(liveTrace.pendingClarify ?? activeTrace.pendingClarify);
    let sid = activeSessionId;
    let createdSession = false;
    if (!sid) {
      try {
        const s = await api.createSession();
        sid = s.id;
        createdSession = true;
      } catch (e) {
        push("error", (e as Error).message);
        return;
      }
    }
    sendSessionId.current = sid;
    const userMessageId = crypto.randomUUID();
    const pendingMessageId = crypto.randomUUID();
    const now = new Date().toISOString();
    setMessages((prev) => [
      ...prev,
      { id: userMessageId, role: "user", text, ts: now, status: "pending" },
      { id: pendingMessageId, role: "assistant", text: "", ts: now, status: "pending" },
    ]);
    setSubmitting(true);
    setRunDetail(null);
    setErrorBanner(null);
    try {
      const { run_id } = await api.sendMessage(sid, text);
      if (createdSession) {
        navigate(ROUTES.chat(sid), { replace: true });
        void api.getSession(sid).then((record) => {
          setSessions((prev) => [record, ...prev.filter((s) => s.id !== record.id)]);
        }).catch(() => undefined);
      }
      completedBubbleFor.current = null;
      failedBubbleFor.current = null;
      if (wasAwaitingClarify) {
        const traceEvents = liveTrace.events.length > 0 ? liveTrace.events : activeTrace.events;
        clarifyResumeSinceSeq.current = traceEvents.reduce(
          (max, event) => Math.max(max, event.seq ?? 0),
          0,
        );
        setClarifyResuming(true);
      }
      setLiveRunGeneration((gen) => gen + 1);
      setMessages((prev) => prev.map((message) =>
        message.id === userMessageId || message.id === pendingMessageId
          ? { ...message, runId: run_id, status: "streaming" }
          : message));
      setInspectedRunId(run_id);
      setLiveRunId(run_id);
    } catch (e) {
      if (createdSession && sid) {
        await api.deleteSession(sid).catch(() => undefined);
        sendSessionId.current = null;
      }
      setMessages((prev) => prev.map((message) =>
        message.id === pendingMessageId
          ? {
              ...message,
              text: `I couldn't start that request: ${(e as Error).message}`,
              error: true,
              status: "failed",
            }
          : message));
    } finally {
      setSubmitting(false);
    }
  }, [activeSessionId, activeTrace.pendingClarify, liveTrace.pendingClarify, navigate, push]);

  // Parked clarify: show the question as a completed assistant turn (not in-flight synthesis).
  useEffect(() => {
    if (!liveRunId || !liveTrace.pendingClarify || clarifyResuming) return;
    const lastClarify = [...liveTrace.events].reverse()
      .find((event) => event.type === EventType.ClarifyRequired);
    if (lastClarify && (lastClarify.seq ?? 0) <= clarifyResumeSinceSeq.current) return;
    if (sendSessionId.current && sendSessionId.current !== activeSessionId) return;
    upsertAssistant(
      liveRunId,
      liveTrace.pendingClarify,
      new Date().toISOString(),
      "completed",
    );
  }, [liveRunId, liveTrace.pendingClarify, liveTrace.events, clarifyResuming, activeSessionId, upsertAssistant]);

  useEffect(() => {
    if (!clarifyResuming) return;
    const afterResume = liveTrace.events.filter(
      (event) => (event.seq ?? 0) > clarifyResumeSinceSeq.current,
    );
    if (afterResume.length === 0) return;
    const terminal = afterResume.some((event) =>
      event.type === EventType.RunFinished || event.type === EventType.RunFailed);
    const resumed = afterResume.some((event) =>
      event.type === EventType.PlanCreated || event.type === EventType.AgentSpawned);
    const reClarify = afterResume.some((event) => event.type === EventType.ClarifyRequired);
    if (terminal || resumed || reClarify) {
      setClarifyResuming(false);
    }
  }, [clarifyResuming, liveTrace.events]);

  // Stream answer tokens into the live bubble as they arrive.
  useEffect(() => {
    if (!liveRunId || !liveTrace.partialAnswer) return;
    if (liveTrace.status === "completed") return;
    if (liveTrace.pendingClarify && !clarifyResuming) return;
    if (sendSessionId.current && sendSessionId.current !== activeSessionId) return;
    upsertAssistant(liveRunId, liveTrace.partialAnswer, new Date().toISOString(), "streaming");
  }, [
    liveRunId,
    liveTrace.partialAnswer,
    liveTrace.status,
    liveTrace.pendingClarify,
    clarifyResuming,
    activeSessionId,
    upsertAssistant,
  ]);

  // Finish the in-flight send only. Replaying another trace must not append
  // that answer under the conversation that is already open.
  useEffect(() => {
    if (!liveRunId) return;
    if (sendSessionId.current && sendSessionId.current !== activeSessionId) return;
    const finished = [...liveTrace.events].reverse()
      .find((e) => e.type === EventType.RunFinished);
    const text = (finished && "result" in finished && finished.result)
      || liveTrace.partialAnswer
      || "";
    if (liveTrace.status === "completed" && text) {
      upsertAssistant(
        liveRunId,
        text,
        finished && "ts" in finished ? finished.ts : new Date().toISOString(),
        "completed",
      );
    }
  }, [
    liveRunId,
    liveTrace.status,
    liveTrace.partialAnswer,
    liveTrace.events,
    activeSessionId,
    upsertAssistant,
  ]);

  useEffect(() => {
    if (!liveRunId || liveTrace.status !== "completed") return;
    const sessionId = sendSessionId.current;
    const finished = [...liveTrace.events].reverse().find((e) => e.type === EventType.RunFinished);
    const text = (finished && "result" in finished && finished.result) || liveTrace.partialAnswer;
    const sameConversation = !sessionId || sessionId === activeSessionId;

    const runId = liveRunId;
    setLiveRunId(null);
    void (async () => {
      if (!text && sameConversation) {
        try {
          const detail = await api.runDetail(runId);
          if (detail.result) {
            upsertAssistant(
              runId,
              detail.result,
              detail.finished_at ?? new Date().toISOString(),
              "completed",
            );
          }
        } catch { /* ignore */ }
      }
      if (sessionId && sameConversation) {
        await refreshSessionMessages(sessionId);
        void autoRuns(sessionId).then((r) => setRuns(r.runs));
        void api.listSessions().then(setSessions).catch(() => undefined);
      }
    })();
  }, [
    liveTrace.events, liveTrace.status, liveTrace.partialAnswer, liveRunId,
    activeSessionId, upsertAssistant, refreshSessionMessages, autoRuns,
  ]);

  useEffect(() => {
    if (!liveRunId || liveTrace.status !== "failed") return;
    if (sendSessionId.current && sendSessionId.current !== activeSessionId) return;
    if (failedBubbleFor.current === liveRunId) return;
    failedBubbleFor.current = liveRunId;
    const failedEv = [...liveTrace.events].reverse().find((e) => e.type === EventType.RunFailed);
    const why = failedEv && "error" in failedEv ? failedEv.error : "unknown error";
    setMessages((prev) => {
      if (prev.some((m) => m.error && m.text.includes(why) && m.runId === liveRunId)) return prev;
      const pending = prev.findIndex((message) =>
        message.role === "assistant" && message.runId === liveRunId
        && (message.status === "streaming" || message.status === "pending"));
      if (pending >= 0) {
        return prev.map((message, index) => {
          if (message.runId !== liveRunId) return message;
          if (index === pending) {
            return {
              ...message,
              text: `I couldn't complete that request: ${why}. Please try again.`,
              error: true,
              status: "failed" as const,
              ts: new Date().toISOString(),
            };
          }
          return message.role === "user" ? { ...message, status: "failed" as const } : message;
        });
      }
      return [...prev, {
        id: `failed-${liveRunId}`,
        role: "assistant",
        text: `I couldn't complete that request: ${why}. Please try again.`,
        error: true,
        runId: liveRunId,
        status: "failed",
        ts: new Date().toISOString(),
      }];
    });
    if (sendSessionId.current) {
      void refreshSessionMessages(sendSessionId.current);
      void autoRuns(sendSessionId.current).then((r) => setRuns(r.runs));
    }
    setLiveRunId(null);
  }, [liveTrace.status, liveTrace.events, liveRunId, activeSessionId, refreshSessionMessages, autoRuns]);

  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;
    const apply = (detail: RunDetail) => {
      if (cancelled) return;
      setRunDetail(detail);
      if (detail.result && detail.session_id === activeSessionId
          && (detail.status === "completed" || detail.status === "failed")) {
        upsertAssistant(
          detail.id,
          detail.result,
          detail.finished_at ?? new Date().toISOString(),
          "completed",
        );
      }
    };
    void api.runDetail(activeRunId).then(apply).catch(() => undefined);
    return () => { cancelled = true; };
  }, [activeRunId, activeTrace.status, activeSessionId, upsertAssistant]);

  const plan = useMemo<PlanShape | null>(() => {
    const created = [...activeTrace.events].reverse().find((e) => e.type === EventType.PlanCreated);
    if (created && "plan" in created) {
      return { steps: created.plan.steps, synthesis: created.plan.synthesis };
    }
    if (runDetail?.plan?.steps) {
      return {
        steps: runDetail.plan.steps.map((step) => ({
          id: step.id,
          agent: step.agent,
          task: step.task,
          tools: step.tools ?? [],
          depends_on: step.depends_on ?? [],
        })),
        synthesis: "Synthesize the final answer",
      };
    }
    return null;
  }, [activeTrace.events, runDetail]);

  const approvalEvent = useMemo(
    () => [...activeTrace.events].reverse().find((e) => e.type === EventType.ApprovalRequired),
    [activeTrace.events],
  );
  const approvalReason = approvalEvent && "reason" in approvalEvent ? approvalEvent.reason : null;

  const rawPendingClarify = liveTrace.pendingClarify ?? activeTrace.pendingClarify;
  const pendingClarify = rawPendingClarify && !clarifyResuming ? rawPendingClarify : null;
  const awaitingUserInput = Boolean(pendingClarify);
  const streaming = submitting || clarifyResuming
    || (!awaitingUserInput && (liveTrace.status === "streaming" || Boolean(liveRunId)));
  const runContext = activeSession
    ? `${activeSession.title} · ${activeSession.total_tokens.toLocaleString()} tok · $${activeSession.total_cost_usd.toFixed(4)}`
    : null;

  const onApprove = useCallback((approved: boolean) => {
    const fn = (liveRunId && inspectedRunId === liveRunId)
      ? liveTrace.approve
      : activeTrace.approve;
    void fn(approved).catch((error) => push("error", (error as Error).message));
  }, [liveRunId, inspectedRunId, liveTrace.approve, activeTrace.approve, push]);

  return (
    <div className="app">
      <a className="skip-link" href="#conversation">Skip to conversation</a>
      <TopBar
        runContext={inspectorOpen ? runContext : null}
        detailLevel={detailLevel}
        historyOpen={sidebarOpen}
        telemetryOpen={telemetryOpen}
        theme={theme}
        contextStats={contextStats}
        contextStatsLoading={contextStatsLoading}
        onToggleHistory={() => {
          setSidebarOpen((open) => !open);
          setTelemetryOpen(false);
        }}
        onToggleTelemetry={() => {
          setTelemetryOpen((open) => !open);
          setSidebarOpen(false);
          if (detailLevel === "technical") setDetailLevel("simple");
        }}
        onToggleTheme={toggleTheme}
        onDetailLevelChange={(level) => {
          setDetailLevel(level);
          if (level === "technical") setTelemetryOpen(false);
        }}
      />
      <div className="workspace" data-detail={detailLevel}>
        <div className="stage">
          <ChatPane
            messages={messages}
            historyLoading={historyLoading}
            submitting={submitting}
            streaming={streaming}
            status={activeTrace.status}
            events={activeTrace.events}
            liveRunId={liveRunId}
            liveEvents={liveTrace.events}
            liveStatus={liveTrace.status}
            detailLevel={detailLevel}
            pendingApproval={activeTrace.pendingApproval}
            pendingClarify={pendingClarify}
            awaitingUserInput={awaitingUserInput}
            approvalReason={approvalReason}
            activeRunId={activeRunId}
            errorBanner={errorBanner}
            onDismissError={() => setErrorBanner(null)}
            onSend={(t) => void send(t)}
            onShowDetails={() => {
              setTelemetryOpen(false);
              setDetailLevel("technical");
            }}
            onInspectRun={inspectRun}
            onApprove={onApprove}
          />
        </div>

        {(sidebarOpen || sheetMounted || telemetryOpen) && (
          <button
            className="veil"
            aria-label={telemetryOpen ? "Close telemetry" : inspectorOpen ? "Close activity" : "Close conversations"}
            onClick={closeAllOverlays}
          />
        )}

        <Sidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          runs={runs}
          activeRunId={activeRunId}
          open={sidebarOpen}
          detailLevel={detailLevel}
          onClose={() => setSidebarOpen(false)}
          onSelectSession={(id) => void selectSession(id)}
          onNewSession={() => void newSession()}
          onDeleteSession={(id) => void deleteSession(id)}
          onSelectRun={inspectRun}
        />

        {telemetryOpen && (
          <TelemetryDashboard
            open={telemetryOpen}
            onClose={() => setTelemetryOpen(false)}
            onInspectRun={(runId) => void openHistoricalTrace(runId)}
          />
        )}

        {sheetMounted && (
          <Inspector
            open={sheetOpen}
            events={activeTrace.events}
            status={activeTrace.status}
            mode={liveRunId && inspectedRunId === liveRunId ? "live" : "replay"}
            pendingApproval={activeTrace.pendingApproval}
            approvalReason={approvalReason}
            runDetail={runDetail}
            plan={plan}
            onClose={() => setDetailLevel("simple")}
            onApprove={onApprove}
            onOpenToolResult={(resultId) =>
              activeRunId && setDrawer({ runId: activeRunId, resultId })}
          />
        )}
      </div>
      {drawer && (
        <ToolResultDrawer
          runId={drawer.runId}
          resultId={drawer.resultId}
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path="/" element={<Workspace />} />
        <Route path="/chat/:sessionId" element={<Workspace />} />
      </Routes>
    </ToastProvider>
  );
}
