/** Toast host + useToasts — top-right transient notifications. */

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

interface Ctx {
  push: (kind: ToastKind, text: string) => void;
}

const ToastCtx = createContext<Ctx | null>(null);

let nextId = 1;

/** App-level provider; pages call useToasts().push(...). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((kind: ToastKind, text: string) => {
    const id = nextId++;
    setToasts((prev) => [...prev, { id, kind, text }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4200);
  }, []);

  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            {t.kind === "success" && <CheckCircle2 size={14} />}
            {t.kind === "error" && <XCircle size={14} />}
            {t.kind === "info" && <Info size={14} />}
            <span className="msg">{t.text}</span>
            <button className="toast-x" aria-label="Dismiss" onClick={() =>
              setToasts((prev) => prev.filter((x) => x.id !== t.id))
            }>
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToasts(): Ctx {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToasts outside ToastProvider");
  return ctx;
}
