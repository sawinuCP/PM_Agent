import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { MessageSquarePlus, Search, Trash2, X } from "lucide-react";

import { fmtUsd, relTime } from "../api/format";
import type { RunSummary, SessionOut } from "../api/types";
import type { DetailLevel } from "../hooks/useDetailLevel";
import { ROUTES } from "../routes";
import { statusTone } from "./shared/eventMeta";

interface Props {
  sessions: SessionOut[];
  activeSessionId: string | null;
  runs: RunSummary[];
  activeRunId: string | null;
  open: boolean;
  detailLevel: DetailLevel;
  onClose: () => void;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  onSelectRun: (id: string) => void;
}

export function Sidebar(p: Props) {
  const [query, setQuery] = useState("");
  const railRef = useRef<HTMLElement>(null);
  const filtered = useMemo(
    () => p.sessions.filter((s) => s.title.toLowerCase().includes(query.toLowerCase())),
    [p.sessions, query],
  );

  useEffect(() => {
    if (railRef.current) railRef.current.inert = !p.open;
  }, [p.open]);

  return (
    <aside
      ref={railRef}
      className={`panel panel-sidebar ${p.open ? "open" : ""}`}
      aria-label="Conversations"
      aria-hidden={!p.open}
    >
      <div className="panel-head sidebar-head">
        <div className="sidebar-head-row">
          <h2 className="panel-title" style={{ flex: 1 }}>
            {p.detailLevel === "simple" ? "Conversations" : "Sessions"}
          </h2>
          <button className="btn btn-primary btn-sm" onClick={p.onNewSession}>
            <MessageSquarePlus size={12} /> New
          </button>
          <button
            className="btn btn-ghost btn-sm btn-icon"
            aria-label="Close conversations"
            onClick={p.onClose}
          >
            <X size={14} />
          </button>
        </div>
        <div className="input-wrap">
          <Search size={13} />
          <input
            className="input"
            placeholder={p.detailLevel === "simple" ? "Search conversations…" : "Search sessions…"}
            aria-label={p.detailLevel === "simple" ? "Search conversations" : "Search sessions"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <div className="panel-body">
        {filtered.length === 0 ? (
          <div className="state-block" style={{ minHeight: 120 }}>
            <div className="state-title">{query ? "No matches" : `No ${p.detailLevel === "simple" ? "conversations" : "sessions"} yet`}</div>
            <div className="state-desc">
              {query ? "Try a different search term." : p.detailLevel === "simple"
                ? "Start a new conversation whenever you're ready."
                : "Create a session and give the agents a goal."}
            </div>
          </div>
        ) : (
          <div className="session-list">
            {filtered.map((s) => (
              <div key={s.id}>
                <NavLink
                  to={ROUTES.chat(s.id)}
                  className={({ isActive }) => `session-item ${isActive ? "active" : ""}`}
                  aria-current={s.id === p.activeSessionId ? "page" : undefined}
                  onClick={() => p.onSelectSession(s.id)}
                >
                  <div className="session-title">{s.title}</div>
                  <div className="session-meta">
                    <span>{s.message_count} msgs</span>
                    {p.detailLevel === "technical" && (
                      <><span className="sep">·</span><span>{fmtUsd(s.total_cost_usd)}</span></>
                    )}
                    <span className="sep">·</span>
                    <span>{relTime(s.updated_at)}</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm btn-icon session-delete"
                    title="Delete session" aria-label={`Delete ${s.title}`}
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); p.onDeleteSession(s.id); }}
                  >
                    <Trash2 size={12} />
                  </button>
                </NavLink>
                {p.detailLevel === "technical" && s.id === p.activeSessionId && p.runs.length > 0 && (
                  <div className="runs-block">
                    <div className="runs-label">Runs</div>
                    {p.runs.map((r) => (
                      <div
                        key={r.id}
                        className={`run-item ${r.id === p.activeRunId ? "active" : ""}`}
                        role="button" tabIndex={0}
                        onClick={() => p.onSelectRun(r.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            p.onSelectRun(r.id);
                          }
                        }}
                      >
                        <span className={`status-dot ${statusTone(r.status)} ${r.status === "running" ? "pulse" : ""}`} />
                        <span className="run-goal" title={r.goal}>{r.goal}</span>
                        <span className="run-cost">{fmtUsd(r.cost_usd)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
