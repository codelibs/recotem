const rawApiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export const API_BASE_URL = rawApiBaseUrl.replace(/\/+$/, "");

export function toApiUrl(path: string): string {
  const normalizedPath = path.replace(/^\/+/, "");
  return `${API_BASE_URL}/${normalizedPath}`;
}

/**
 * Build a WebSocket URL from the environment.
 * If VITE_WS_BASE_URL is set, use it directly.
 * Otherwise, derive from the current page location.
 */
export function buildWsBaseUrl(): string {
  const explicit = import.meta.env.VITE_WS_BASE_URL;
  if (explicit) return explicit.replace(/\/+$/, "");

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}`;
}

// Startup validation — log warnings for misconfigured env
if (import.meta.env.DEV) {
  if (!rawApiBaseUrl) {
    console.warn("[recotem] VITE_API_BASE_URL is empty — using default /api/v1");
  }
}
