import { useEffect, useState } from "react";
import { Fingerprint, Package, Settings2, X } from "lucide-react";

import { api } from "../api/client";
import type { ToolResultDetail } from "../api/types";
import { EmptyState } from "./ui/EmptyState";
import { StatusBadge } from "./ui/StatusBadge";
import { toolIcon } from "./shared/agentMeta";

interface Props {
  runId: string;
  resultId: string;
  onClose: () => void;
}

/** ADR-07 drill-down: full tool payload behind the agent-facing digest. */
export function ToolResultDrawer({ runId, resultId, onClose }: Props) {
  const [data, setData] = useState<ToolResultDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    api.toolResult(runId, resultId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      window.removeEventListener("keydown", onKey);
    };
  }, [runId, resultId, onClose]);

  const { Icon: TIcon, color } = toolIcon(data?.tool ?? "");

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Tool result detail">
        <div className="drawer-head">
          {data && <TIcon size={16} style={{ color }} />}
          <span className="drawer-title">{data?.tool ?? "Tool result"}</span>
          {data && <StatusBadge status={data.status} />}
          <button className="btn btn-ghost btn-icon" style={{ marginLeft: "auto" }} aria-label="Close" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <div className="drawer-body">
          {error && (
            <EmptyState icon={X} title="Failed to load result" desc={error} />
          )}
          {!error && !data && (
            <div className="state-block"><span className="spinner" /> Loading…</div>
          )}
          {data && (
            <>
              <section className="drawer-section">
                <h3 className="drawer-section-title"><Settings2 size={12} /> Details</h3>
                <dl className="kv-grid">
                  <dt>step</dt><dd>{data.step_id}</dd>
                  <dt>status</dt><dd>{data.status}</dd>
                  <dt>byte size</dt><dd>{data.byte_size.toLocaleString()}</dd>
                  <dt>digest</dt><dd>{data.digest.slice(0, 24)}…</dd>
                  <dt>recorded</dt><dd>{new Date(data.created_at).toLocaleString()}</dd>
                </dl>
              </section>

              <section className="drawer-section">
                <h3 className="drawer-section-title"><Fingerprint size={12} /> Digest (agent-facing)</h3>
                <pre className="code-block" style={{ maxHeight: 150 }}>{data.digest}</pre>
              </section>

              <section className="drawer-section">
                <h3 className="drawer-section-title"><Settings2 size={12} /> Arguments</h3>
                <pre className="code-block" style={{ maxHeight: 170 }}>
                  {JSON.stringify(data.args_digest, null, 2)}
                </pre>
              </section>

              <section className="drawer-section">
                <h3 className="drawer-section-title"><Package size={12} /> Full payload</h3>
                <pre className="code-block">
                  {data.payload == null
                    ? "(payload not stored — size cap exceeded; digest above is the authoritative record)"
                    : JSON.stringify(data.payload, null, 2)}
                </pre>
              </section>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
