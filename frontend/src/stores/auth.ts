import { ofetch } from "ofetch";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { toApiUrl } from "@/api/config";
import type { User } from "@/types";

export const useAuthStore = defineStore("auth", () => {
  const accessToken = ref<string | null>(localStorage.getItem("access_token"));
  const refreshToken = ref<string | null>(localStorage.getItem("refresh_token"));
  const user = ref<User | null>(null);

  const isAuthenticated = computed(() => !!accessToken.value);

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
    accessToken.value = null;
    refreshToken.value = null;
    user.value = null;
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
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
