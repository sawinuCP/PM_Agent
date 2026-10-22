import { describe, expect, it } from "vitest";

import {
  clampInspectorWidth,
  DEFAULT_INSPECTOR_WIDTH,
  MIN_INSPECTOR_WIDTH,
  readStoredInspectorWidth,
} from "./usePanelWidth";

describe("inspector panel width", () => {
  it("defaults to a wide sheet when nothing is stored", () => {
    expect(readStoredInspectorWidth(null)).toBe(DEFAULT_INSPECTOR_WIDTH);
    expect(readStoredInspectorWidth({ getItem: () => null })).toBe(DEFAULT_INSPECTOR_WIDTH);
  });

  it("restores a saved width and clamps it to the viewport", () => {
    expect(readStoredInspectorWidth({ getItem: () => "680" })).toBe(680);
    expect(readStoredInspectorWidth({ getItem: () => "not-a-number" })).toBe(DEFAULT_INSPECTOR_WIDTH);
    expect(clampInspectorWidth(200, 1280)).toBe(MIN_INSPECTOR_WIDTH);
    expect(clampInspectorWidth(4000, 1280)).toBe(1232);
  });

  it("degrades safely when storage is unavailable", () => {
    expect(readStoredInspectorWidth({
      getItem: () => { throw new Error("blocked"); },
    })).toBe(DEFAULT_INSPECTOR_WIDTH);
  });
});
