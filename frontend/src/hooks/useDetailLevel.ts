import { useState } from "react";

export type DetailLevel = "simple" | "technical";

const STORAGE_KEY = "azend.detailLevel";

export function readStoredDetailLevel(
  storage: Pick<Storage, "getItem"> | null,
): DetailLevel {
  try {
    return storage?.getItem(STORAGE_KEY) === "technical" ? "technical" : "simple";
  } catch {
    return "simple";
  }
}

export function useDetailLevel() {
  const [detailLevel, setLevel] = useState<DetailLevel>(() =>
    readStoredDetailLevel(typeof window === "undefined" ? null : window.localStorage));

  const setDetailLevel = (level: DetailLevel): void => {
    setLevel(level);
    try {
      window.localStorage.setItem(STORAGE_KEY, level);
    } catch {
      // Storage may be unavailable in privacy modes; in-memory state still works.
    }
  };

  return { detailLevel, setDetailLevel };
}
