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

export const api = ofetch.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
  },
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
  },
});
