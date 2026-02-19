import { ofetch } from "ofetch";
import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";
import { toApiUrl } from "@/api/config";
import type { User } from "@/types";

const MIN_REFRESH_MARGIN_MS = 30 * 1000; // Never refresh less than 30s before expiry

function parseJwtExpirySeconds(token: string): number {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.exp === "number" ? payload.exp : 0;
  } catch {
    return 0;
  }
}

/**
 * Calculate how long to wait before refreshing the token.
 * Refreshes at 75% of the remaining lifetime, but never less than
 * MIN_REFRESH_MARGIN_MS before expiry.
 */
export function calcRefreshDelay(expMs: number, nowMs: number = Date.now()): number {
  const remaining = expMs - nowMs;
  if (remaining <= 0) return 0;
  // Refresh at 75% of remaining lifetime
  const atSeventyFive = remaining * 0.75;
  // But ensure at least MIN_REFRESH_MARGIN_MS before expiry
  const atMargin = remaining - MIN_REFRESH_MARGIN_MS;
  if (atMargin <= 0) return 0;
  return Math.min(atSeventyFive, atMargin);
}

export const useAuthStore = defineStore("auth", () => {
  const tokenExpiry = ref<number | null>(
    sessionStorage.getItem("token_expiry")
      ? parseInt(sessionStorage.getItem("token_expiry")!, 10)
      : null,
  );
  const user = ref<User | null>(null);
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;

  const isAuthenticated = computed(() => {
    if (!tokenExpiry.value) return false;
    return tokenExpiry.value > Math.floor(Date.now() / 1000);
  });

  function scheduleProactiveRefresh() {
    if (refreshTimer) {
      clearTimeout(refreshTimer);
      refreshTimer = null;
    }
    if (!tokenExpiry.value) return;
    const expMs = tokenExpiry.value * 1000;
    const delay = calcRefreshDelay(expMs);
    if (delay <= 0) {
      refreshAccessToken();
      return;
    }
    refreshTimer = setTimeout(() => {
      refreshAccessToken();
    }, delay);
  }

  watch(tokenExpiry, () => {
    scheduleProactiveRefresh();
  });

  async function login(username: string, password: string) {
    const response = await ofetch(toApiUrl("/auth/login/"), {
      method: "POST",
      body: { username, password },
    });
    const expiry = parseJwtExpirySeconds(response.access);
    tokenExpiry.value = expiry;
    sessionStorage.setItem("token_expiry", String(expiry));
    await fetchUser();
  }

  async function fetchUser() {
    if (!tokenExpiry.value) return;
    try {
      user.value = await ofetch(toApiUrl("/auth/user/"));
    } catch {
      await logout();
    }
  }

  async function logout() {
    try {
      await ofetch(toApiUrl("/auth/logout/"), {
        method: "POST",
      }).catch(() => {
        // Best-effort server-side logout; ignore errors
      });
    } finally {
      if (refreshTimer) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
      }
      tokenExpiry.value = null;
      user.value = null;
      sessionStorage.removeItem("token_expiry");
    }
  }

  async function refreshAccessToken() {
    if (!tokenExpiry.value) {
      await logout();
      return;
    }
    try {
      const response = await ofetch(toApiUrl("/auth/token/refresh/"), {
        method: "POST",
        timeout: 5000,
      });
      const expiry = parseJwtExpirySeconds(response.access);
      tokenExpiry.value = expiry;
      sessionStorage.setItem("token_expiry", String(expiry));
    } catch {
      await logout();
    }
  }

  async function ensureFreshToken(): Promise<boolean> {
    if (!tokenExpiry.value) return false;
    const nowSeconds = Math.floor(Date.now() / 1000);
    if (tokenExpiry.value - nowSeconds < 60) {
      await refreshAccessToken();
      return !!tokenExpiry.value;
    }
    return true;
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "visible" && tokenExpiry.value) {
      scheduleProactiveRefresh();
    }
  }
  document.addEventListener("visibilitychange", handleVisibilityChange);

  // Schedule initial proactive refresh if already authenticated
  scheduleProactiveRefresh();

  return {
    tokenExpiry,
    user,
    isAuthenticated,
    login,
    logout,
    fetchUser,
    refreshAccessToken,
    ensureFreshToken,
  };
});
