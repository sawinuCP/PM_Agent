import { useCallback, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

export const INSPECTOR_WIDTH_KEY = "azend.inspectorWidth";
export const DEFAULT_INSPECTOR_WIDTH = 720;
export const MIN_INSPECTOR_WIDTH = 440;

export function clampInspectorWidth(
  width: number,
  viewport = typeof window === "undefined" ? 1440 : window.innerWidth,
): number {
  const max = Math.max(MIN_INSPECTOR_WIDTH, viewport - 48);
  return Math.round(Math.min(max, Math.max(MIN_INSPECTOR_WIDTH, width)));
}

export function readStoredInspectorWidth(
  storage: Pick<Storage, "getItem"> | null,
): number {
  try {
    const raw = storage?.getItem(INSPECTOR_WIDTH_KEY);
    if (!raw) return DEFAULT_INSPECTOR_WIDTH;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return DEFAULT_INSPECTOR_WIDTH;
    return clampInspectorWidth(parsed);
  } catch {
    return DEFAULT_INSPECTOR_WIDTH;
  }
}

function persistWidth(width: number): void {
  try {
    window.localStorage.setItem(INSPECTOR_WIDTH_KEY, String(width));
  } catch {
    // Privacy modes can block storage; in-memory width still works.
  }
}

export function usePanelWidth() {
  const [width, setWidth] = useState(() =>
    readStoredInspectorWidth(typeof window === "undefined" ? null : window.localStorage));
  const [resizing, setResizing] = useState(false);
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const widthRef = useRef(width);
  widthRef.current = width;

  const commit = useCallback((next: number, store = true) => {
    const clamped = clampInspectorWidth(next);
    widthRef.current = clamped;
    setWidth(clamped);
    if (store) persistWidth(clamped);
    return clamped;
  }, []);

  const onResizePointerDown = useCallback((event: PointerEvent<HTMLElement>) => {
    event.preventDefault();
    dragRef.current = { startX: event.clientX, startWidth: widthRef.current };
    setResizing(true);
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);

    const onMove = (move: globalThis.PointerEvent): void => {
      const drag = dragRef.current;
      if (!drag) return;
      commit(drag.startWidth + (drag.startX - move.clientX), false);
    };
    const onUp = (): void => {
      persistWidth(widthRef.current);
      dragRef.current = null;
      setResizing(false);
      handle.releasePointerCapture(event.pointerId);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, [commit]);

  const onResizeKeyDown = useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      commit(width + 32);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      commit(width - 32);
    } else if (event.key === "Home") {
      event.preventDefault();
      commit(MIN_INSPECTOR_WIDTH);
    } else if (event.key === "End") {
      event.preventDefault();
      commit(window.innerWidth - 48);
    }
  }, [commit, width]);

  return { width, resizing, onResizePointerDown, onResizeKeyDown };
}
