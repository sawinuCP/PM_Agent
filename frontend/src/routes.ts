/** URL paths for chat session navigation. */

export const ROUTES = {
  newChat: "/",
  chat: (sessionId: string) => `/chat/${sessionId}`,
} as const;

export function chatSessionIdFromPath(pathname: string): string | null {
  const match = /^\/chat\/([^/]+)$/.exec(pathname);
  return match?.[1] ?? null;
}
