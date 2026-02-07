import { ofetch, type FetchOptions } from "ofetch";
import { useAuthStore } from "@/stores/auth";
import { API_BASE_URL } from "./config";

let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

async function attemptRefresh(): Promise<boolean> {
  if (isRefreshing && refreshPromise) {
    return refreshPromise;
  }
  isRefreshing = true;
  refreshPromise = (async () => {
    try {
      const authStore = useAuthStore();
      await authStore.refreshAccessToken();
      return !!authStore.accessToken;
    } finally {
      isRefreshing = false;
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

const MAX_RETRIES = 3;
const MAX_RETRY_TOTAL_MS = 5000;
const REQUEST_TIMEOUT_MS = 30_000;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export const api = ofetch.create({
  baseURL: API_BASE_URL,
  timeout: REQUEST_TIMEOUT_MS,
  headers: {
    "Content-Type": "application/json",
  },
  retry: false,
  onRequest({ options }) {
    const authStore = useAuthStore();
    if (authStore.accessToken) {
      const headers = new Headers(options.headers as HeadersInit);
      headers.set("Authorization", `Bearer ${authStore.accessToken}`);
      options.headers = headers;
    }
  },
  async onResponseError({ request, response, options }) {
    if (response.status === 401) {
      const authStore = useAuthStore();
      // Skip refresh for auth endpoints to avoid infinite loops
      const url = typeof request === "string" ? request : request?.toString?.() ?? "";
      if (url.includes("/auth/token/refresh") || url.includes("/auth/login")) {
        await authStore.logout();
        return;
      }

      const refreshed = await attemptRefresh();
      if (refreshed) {
        // Retry the original request with new token
        const headers = new Headers(options.headers as HeadersInit);
        headers.set("Authorization", `Bearer ${authStore.accessToken}`);
        return ofetch(request, {
          ...(options as FetchOptions),
          headers,
        }) as unknown as Promise<void>;
      }
      await authStore.logout();
    }

    // Retry on 5xx server errors with exponential backoff (capped at 5s total)
    // Only retry idempotent methods (GET/HEAD) to avoid duplicate side effects
    if (response.status >= 500) {
      const method = (options.method ?? "GET").toUpperCase();
      if (method !== "GET" && method !== "HEAD") return;
      const retryCount = ((options as any)._retryCount ?? 0) as number;
      const elapsed = ((options as any)._retryElapsed ?? 0) as number;
      if (retryCount < MAX_RETRIES) {
        const backoffMs = Math.min(1000 * 2 ** retryCount, 4000);
        if (elapsed + backoffMs > MAX_RETRY_TOTAL_MS) return;
        await delay(backoffMs);
        return ofetch(request, {
          ...(options as FetchOptions),
          _retryCount: retryCount + 1,
          _retryElapsed: elapsed + backoffMs,
        } as any) as unknown as Promise<void>;
      }
    }
  },
});
