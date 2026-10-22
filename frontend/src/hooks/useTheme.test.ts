import { describe, expect, it } from "vitest";

import { readStoredTheme } from "./useTheme";

describe("theme preference", () => {
  it("restores an explicit stored theme", () => {
    expect(readStoredTheme({ getItem: () => "light" })).toBe("light");
    expect(readStoredTheme({ getItem: () => "dark" })).toBe("dark");
  });

  it("degrades safely when storage is unavailable", () => {
    expect(readStoredTheme({ getItem: () => { throw new Error("blocked"); } })).toBe("dark");
    expect(readStoredTheme(null)).toBe("dark");
  });
});
