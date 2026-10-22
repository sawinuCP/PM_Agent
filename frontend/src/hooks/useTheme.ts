import { useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "azend.theme";

export function readStoredTheme(storage: Pick<Storage, "getItem"> | null): Theme {
  try {
    const stored = storage?.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* privacy mode */
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches) {
    return "light";
  }
  return "dark";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    const next = readStoredTheme(typeof window === "undefined" ? null : window.localStorage);
    if (typeof document !== "undefined") applyTheme(next);
    return next;
  });

  const setTheme = (next: Theme): void => {
    setThemeState(next);
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* in-memory only */
    }
  };

  const toggleTheme = (): void => setTheme(theme === "dark" ? "light" : "dark");

  return { theme, setTheme, toggleTheme };
}
