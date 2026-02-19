import { ofetch } from "ofetch";
import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";
import { toApiUrl } from "@/api/config";
import type { User } from "@/types";

const MIN_REFRESH_MARGIN_MS = 30 * 1000; // Never refresh less than 30s before expiry

function getTokenExpiry(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
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
  const accessToken = ref<string | null>(
    sessionStorage.getItem("access_token"),
  );
  const refreshToken = ref<string | null>(
    sessionStorage.getItem("refresh_token"),
  );
  const user = ref<User | null>(null);
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;

  const isAuthenticated = computed(() => {
    if (!accessToken.value) return false;
    const exp = getTokenExpiry(accessToken.value);
    if (exp !== null && exp < Date.now()) return false;
    return true;
  });

  function scheduleProactiveRefresh() {
    if (refreshTimer) {
      clearTimeout(refreshTimer);
      refreshTimer = null;
    }
    if (!accessToken.value) return;
    const exp = getTokenExpiry(accessToken.value);
    if (exp === null) return;
    const delay = calcRefreshDelay(exp);
    if (delay <= 0) {
      refreshAccessToken();
      return;
    }
    refreshTimer = setTimeout(() => {
      refreshAccessToken();
    }, delay);
  }

  watch(accessToken, () => {
    scheduleProactiveRefresh();
  });

  async function login(username: string, password: string) {
    const response = await ofetch(toApiUrl("/auth/login/"), {
      method: "POST",
      body: { username, password },
    });
    accessToken.value = response.access;
    refreshToken.value = response.refresh;
    sessionStorage.setItem("access_token", response.access);
    sessionStorage.setItem("refresh_token", response.refresh);
    await fetchUser();
  }

  async function fetchUser() {
    if (!accessToken.value) return;
    try {
      user.value = await ofetch(toApiUrl("/auth/user/"), {
        headers: { Authorization: `Bearer ${accessToken.value}` },
      });
    } catch {
      await logout();
    }
  }

  async function logout() {
    try {
      if (refreshToken.value) {
        await ofetch(toApiUrl("/auth/logout/"), {
          method: "POST",
          headers: accessToken.value
            ? { Authorization: `Bearer ${accessToken.value}` }
            : undefined,
          body: { refresh: refreshToken.value },
        }).catch(() => {
          // Best-effort server-side logout; ignore errors
        });
      }
    } finally {
      if (refreshTimer) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
      }
      accessToken.value = null;
      refreshToken.value = null;
      user.value = null;
      sessionStorage.removeItem("access_token");
      sessionStorage.removeItem("refresh_token");
    }
  }

  async function refreshAccessToken() {
    if (!refreshToken.value) {
      await logout();
      return;
    }
    try {
      const response = await ofetch(toApiUrl("/auth/token/refresh/"), {
        method: "POST",
        body: { refresh: refreshToken.value },
        timeout: 5000,
      });
      accessToken.value = response.access;
      sessionStorage.setItem("access_token", response.access);
    } catch {
      await logout();
    }
  }

  async function ensureFreshToken(): Promise<boolean> {
    if (!accessToken.value || !refreshToken.value) return false;
    const exp = getTokenExpiry(accessToken.value);
    if (exp !== null && exp - Date.now() < 60_000) {
      await refreshAccessToken();
      return !!accessToken.value;
    }
    return true;
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "visible" && accessToken.value) {
      scheduleProactiveRefresh();
    }
  }
  document.addEventListener("visibilitychange", handleVisibilityChange);

  // Schedule initial proactive refresh if already authenticated
  scheduleProactiveRefresh();

  return {
    accessToken,
    refreshToken,
    user,
    isAuthenticated,
    login,
    logout,
    fetchUser,
    refreshAccessToken,
    ensureFreshToken,
  };
});
