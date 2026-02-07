import { ofetch } from "ofetch";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { toApiUrl } from "@/api/config";
import type { User } from "@/types";

function getTokenExpiry(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

export const useAuthStore = defineStore("auth", () => {
  const accessToken = ref<string | null>(localStorage.getItem("access_token"));
  const refreshToken = ref<string | null>(localStorage.getItem("refresh_token"));
  const user = ref<User | null>(null);

  const isAuthenticated = computed(() => {
    if (!accessToken.value) return false;
    const exp = getTokenExpiry(accessToken.value);
    if (exp !== null && exp < Date.now()) return false;
    return true;
  });

  async function login(username: string, password: string) {
    const response = await ofetch(toApiUrl("/auth/login/"), {
      method: "POST",
      body: { username, password },
    });
    accessToken.value = response.access;
    refreshToken.value = response.refresh;
    localStorage.setItem("access_token", response.access);
    localStorage.setItem("refresh_token", response.refresh);
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
      accessToken.value = null;
      refreshToken.value = null;
      user.value = null;
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
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
      localStorage.setItem("access_token", response.access);
    } catch {
      await logout();
    }
  }

  return {
    accessToken,
    refreshToken,
    user,
    isAuthenticated,
    login,
    logout,
    fetchUser,
    refreshAccessToken,
  };
});
