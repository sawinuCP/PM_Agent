import { describe, expect, it } from "vitest";

import { readStoredDetailLevel } from "./useDetailLevel";

describe("detail-level preference", () => {
  it("defaults to the simple experience", () => {
    expect(readStoredDetailLevel(null)).toBe("simple");
    expect(readStoredDetailLevel({ getItem: () => null })).toBe("simple");
  });

  it("restores only the explicit technical preference", () => {
    expect(readStoredDetailLevel({ getItem: () => "technical" })).toBe("technical");
    expect(readStoredDetailLevel({ getItem: () => "unexpected" })).toBe("simple");
  });

  it("degrades safely when storage is unavailable", () => {
    expect(readStoredDetailLevel({ getItem: () => { throw new Error("blocked"); } })).toBe("simple");
  });
});
