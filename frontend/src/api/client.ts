import { ofetch, type FetchOptions, FetchError } from "ofetch";
import { useAuthStore } from "@/stores/auth";
import { API_BASE_URL } from "./config";
import type { ApiErrorKind, ClassifiedApiError } from "@/types";
import i18n from "@/i18n";

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

/**
 * Classify a caught error into a structured ApiError with kind, message,
 * and optional field-level errors for validation failures.
 */
function t(key: string): string {
  return i18n.global.t(key);
}

export function classifyApiError(err: unknown): ClassifiedApiError {
  if (err instanceof FetchError) {
    const status = err.response?.status ?? null;
    const body = err.data as Record<string, unknown> | undefined;

    if (status === null) {
      if (err.message?.includes("timeout")) {
        return { kind: "timeout", status: null, message: t("errors.timeout"), raw: err };
      }
      return { kind: "network_error", status: null, message: t("errors.networkError"), raw: err };
    }

    const detail = typeof body?.detail === "string" ? body.detail : undefined;

    const kindMap: Record<number, ApiErrorKind> = {
      400: "validation",
      401: "unauthorized",
      403: "forbidden",
      404: "not_found",
      429: "rate_limited",
    };
    const kind: ApiErrorKind = kindMap[status] ?? (status >= 500 ? "server_error" : "unknown");

    const messages: Record<ApiErrorKind, string> = {
      validation: t("errors.validation"),
      unauthorized: t("errors.unauthorized"),
      forbidden: t("errors.forbidden"),
      not_found: t("errors.notFound"),
      rate_limited: t("errors.rateLimited"),
      server_error: t("errors.serverError"),
      network_error: t("errors.networkError"),
      timeout: t("errors.timeout"),
      unknown: t("errors.unknown"),
    };

    let fieldErrors: Record<string, string[]> | undefined;
    if (kind === "validation" && body) {
      fieldErrors = {};
      for (const [key, val] of Object.entries(body)) {
        if (key === "detail") continue;
        if (Array.isArray(val)) {
          fieldErrors[key] = val.map(String);
        } else if (typeof val === "string") {
          fieldErrors[key] = [val];
        }
      }
      if (Object.keys(fieldErrors).length === 0) fieldErrors = undefined;
    }

    return {
      kind,
      status,
      message: detail ?? messages[kind],
      fieldErrors,
      raw: err,
    };
  }

  return {
    kind: "unknown",
    status: null,
    message: err instanceof Error ? err.message : t("errors.unknown"),
    raw: err,
  };
}

/**
 * Unwrap a paginated DRF response to a plain array.
 * Handles both `{ results: T[] }` and raw `T[]` responses.
 */
export function unwrapResults<T>(res: { results: T[] } | T[]): T[] {
  return Array.isArray(res) ? res : res.results;
}
