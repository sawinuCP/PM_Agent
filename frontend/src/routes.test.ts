import { describe, expect, it } from "vitest";

import { ROUTES, chatSessionIdFromPath } from "./routes";

describe("routes", () => {
  it("builds chat session paths", () => {
    expect(ROUTES.newChat).toBe("/");
    expect(ROUTES.chat("abc-123")).toBe("/chat/abc-123");
  });

  it("parses session ids from chat paths", () => {
    expect(chatSessionIdFromPath("/chat/abc-123")).toBe("abc-123");
    expect(chatSessionIdFromPath("/")).toBeNull();
    expect(chatSessionIdFromPath("/chat/abc/extra")).toBeNull();
  });
});
