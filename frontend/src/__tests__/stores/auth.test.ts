import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore, calcRefreshDelay } from "@/stores/auth";
import type { User } from "@/types";

// Mock ofetch
vi.mock("ofetch", () => ({
  ofetch: vi.fn(),
}));

function makeJwt(expSeconds: number): string {
  const payload = Buffer.from(JSON.stringify({ exp: expSeconds })).toString("base64");
  return `header.${payload}.signature`;
}

describe("useAuthStore", () => {
  let store: ReturnType<typeof useAuthStore>;
  let mockOfetch: any;
  let sessionStorageMock: Record<string, string>;

  beforeEach(async () => {
    const { ofetch } = await import("ofetch");
    mockOfetch = ofetch as any;
    vi.clearAllMocks();
    mockOfetch.mockResolvedValue(undefined);

    setActivePinia(createPinia());

    sessionStorageMock = {};
    vi.stubGlobal("sessionStorage", {
      getItem: vi.fn((key: string) => sessionStorageMock[key] || null),
      setItem: vi.fn((key: string, value: string) => {
        sessionStorageMock[key] = value;
      }),
      removeItem: vi.fn((key: string) => {
        delete sessionStorageMock[key];
      }),
      clear: vi.fn(() => {
        sessionStorageMock = {};
      }),
    });

    store = useAuthStore();
    store.tokenExpiry = null;
    store.user = null;
  });

  describe("login", () => {
    it("stores only expiry in sessionStorage on login, not the token", async () => {
      const expSeconds = Math.floor(Date.now() / 1000) + 300;
      const fakeJwt = makeJwt(expSeconds);
      const mockUser: User = {
        pk: 1,
        username: "testuser",
        email: "test@example.com",
      };

      mockOfetch
        .mockResolvedValueOnce({ access: fakeJwt, refresh: "ignored" })
        .mockResolvedValueOnce(mockUser);

      await store.login("testuser", "password123");

      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/login/", {
        method: "POST",
        body: { username: "testuser", password: "password123" },
      });
      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/user/");

      expect(store.tokenExpiry).toBe(expSeconds);
      expect(store.user).toEqual(mockUser);
      expect(store.isAuthenticated).toBe(true);

      expect(sessionStorage.setItem).toHaveBeenCalledWith(
        "token_expiry",
        String(expSeconds)
      );
      expect(sessionStorageMock["access_token"]).toBeUndefined();
      expect(sessionStorageMock["refresh_token"]).toBeUndefined();
    });

    it("throws error when login fails", async () => {
      mockOfetch.mockRejectedValueOnce(new Error("Invalid credentials"));

      await expect(store.login("testuser", "wrongpass")).rejects.toThrow(
        "Invalid credentials"
      );
      expect(store.tokenExpiry).toBeNull();
      expect(store.user).toBeNull();
      expect(store.isAuthenticated).toBe(false);
    });
  });

  describe("logout", () => {
    it("clears expiry and user from state and sessionStorage", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      store.user = { pk: 1, username: "testuser", email: "test@example.com" };
      sessionStorageMock["token_expiry"] = String(store.tokenExpiry);

      await store.logout();

      expect(store.tokenExpiry).toBeNull();
      expect(store.user).toBeNull();
      expect(store.isAuthenticated).toBe(false);
      expect(sessionStorage.removeItem).toHaveBeenCalledWith("token_expiry");
    });

    it("handles logout when already logged out", async () => {
      await store.logout();
      expect(store.tokenExpiry).toBeNull();
      expect(store.user).toBeNull();
    });
  });

  describe("fetchUser", () => {
    it("fetches and stores user data when tokenExpiry exists", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      const mockUser: User = {
        pk: 1,
        username: "testuser",
        email: "test@example.com",
      };
      mockOfetch.mockResolvedValueOnce(mockUser);

      await store.fetchUser();

      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/user/");
      expect(store.user).toEqual(mockUser);
    });

    it("does not fetch user when no tokenExpiry", async () => {
      store.tokenExpiry = null;

      await store.fetchUser();

      expect(mockOfetch).not.toHaveBeenCalled();
      expect(store.user).toBeNull();
    });

    it("logs out when fetch user fails", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      store.user = { pk: 1, username: "testuser", email: "test@example.com" };
      mockOfetch.mockRejectedValueOnce(new Error("Unauthorized"));

      await store.fetchUser();

      expect(store.tokenExpiry).toBeNull();
      expect(store.user).toBeNull();
    });
  });

  describe("refreshAccessToken", () => {
    it("updates tokenExpiry when refresh succeeds", async () => {
      // Use a long-lived expiry so the proactive refresh watcher
      // schedules a setTimeout rather than calling refresh immediately.
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 600;
      vi.clearAllMocks();

      const expSeconds = Math.floor(Date.now() / 1000) + 300;
      mockOfetch.mockResolvedValueOnce({ access: makeJwt(expSeconds) });

      await store.refreshAccessToken();

      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/token/refresh/", {
        method: "POST",
        timeout: 5000,
      });
      expect(store.tokenExpiry).toBe(expSeconds);
      expect(sessionStorage.setItem).toHaveBeenCalledWith(
        "token_expiry",
        String(expSeconds)
      );
    });

    it("logs out when tokenExpiry is missing", async () => {
      store.tokenExpiry = null;

      await store.refreshAccessToken();

      // No refresh request — only the logout POST is allowed
      const refreshCalls = mockOfetch.mock.calls.filter(
        (c: any[]) => String(c[0]).includes("token/refresh"),
      );
      expect(refreshCalls).toHaveLength(0);
      expect(store.tokenExpiry).toBeNull();
    });

    it("logs out when refresh fails", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 10;
      mockOfetch.mockRejectedValueOnce(new Error("Refresh token expired"));

      await store.refreshAccessToken();

      expect(store.tokenExpiry).toBeNull();
      expect(sessionStorage.removeItem).toHaveBeenCalledWith("token_expiry");
    });
  });

  describe("isAuthenticated", () => {
    it("returns true when tokenExpiry is in the future", () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 60;
      expect(store.isAuthenticated).toBe(true);
    });

    it("returns false when tokenExpiry is null", () => {
      store.tokenExpiry = null;
      expect(store.isAuthenticated).toBe(false);
    });
  });

  describe("ensureFreshToken", () => {
    it("returns true when token has more than 60s remaining", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 300;

      const result = await store.ensureFreshToken();

      expect(result).toBe(true);
      expect(mockOfetch).not.toHaveBeenCalled();
    });

    it("refreshes and returns true when token expires within 60s", async () => {
      const expSeconds = Math.floor(Date.now() / 1000) + 300;
      mockOfetch.mockResolvedValueOnce({ access: makeJwt(expSeconds) });
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 45;

      const result = await store.ensureFreshToken();

      expect(result).toBe(true);
      expect(mockOfetch).toHaveBeenCalledWith(
        "/api/v1/auth/token/refresh/",
        expect.objectContaining({ method: "POST" }),
      );
      expect(store.tokenExpiry).toBe(expSeconds);
    });

    it("returns false when no tokenExpiry", async () => {
      store.tokenExpiry = null;

      const result = await store.ensureFreshToken();

      expect(result).toBe(false);
      expect(mockOfetch).not.toHaveBeenCalled();
    });

    it("returns false when refresh fails", async () => {
      mockOfetch.mockRejectedValueOnce(new Error("Refresh failed"));
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 45;

      const result = await store.ensureFreshToken();

      expect(result).toBe(false);
      expect(store.tokenExpiry).toBeNull();
    });
  });

  describe("visibilitychange", () => {
    it("triggers refresh when tab becomes visible with expired token", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) - 5;
      const expSeconds = Math.floor(Date.now() / 1000) + 300;
      mockOfetch.mockResolvedValueOnce({ access: makeJwt(expSeconds) });

      document.dispatchEvent(new Event("visibilitychange"));

      await vi.waitFor(() => {
        expect(mockOfetch).toHaveBeenCalledWith(
          "/api/v1/auth/token/refresh/",
          expect.objectContaining({ method: "POST" }),
        );
      });
    });

    it("does not trigger refresh when tab becomes visible without token", () => {
      store.tokenExpiry = null;
      mockOfetch.mockClear();

      document.dispatchEvent(new Event("visibilitychange"));

      expect(mockOfetch).not.toHaveBeenCalled();
    });
  });

  describe("initialization", () => {
    it("loads tokenExpiry from sessionStorage on creation", () => {
      sessionStorageMock["token_expiry"] = "123";

      setActivePinia(createPinia());
      const newStore = useAuthStore();

      expect(newStore.tokenExpiry).toBe(123);
    });

    it("initialises tokenExpiry as null when sessionStorage is empty", () => {
      setActivePinia(createPinia());
      const newStore = useAuthStore();
      expect(newStore.tokenExpiry).toBeNull();
    });
  });

  describe("isAuthenticated edge cases", () => {
    it("returns false when tokenExpiry is in the past", () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) - 60;
      expect(store.isAuthenticated).toBe(false);
    });

    it("returns false when tokenExpiry is exactly now", () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000);
      expect(store.isAuthenticated).toBe(false);
    });

    it("returns false when tokenExpiry is 0", () => {
      store.tokenExpiry = 0;
      expect(store.isAuthenticated).toBe(false);
    });
  });

  describe("JWT expiry parsing edge cases", () => {
    it("handles malformed JWT (missing payload) gracefully", async () => {
      mockOfetch
        .mockResolvedValueOnce({ access: "header-only", refresh: "x" })
        .mockResolvedValueOnce({ pk: 1, username: "u", email: "e" });

      await store.login("u", "p");

      // parseJwtExpirySeconds returns 0 for malformed tokens
      expect(store.tokenExpiry).toBe(0);
    });

    it("handles JWT with non-JSON payload", async () => {
      const badPayload = Buffer.from("not-json").toString("base64");
      mockOfetch
        .mockResolvedValueOnce({
          access: `h.${badPayload}.s`,
          refresh: "x",
        })
        .mockResolvedValueOnce({ pk: 1, username: "u", email: "e" });

      await store.login("u", "p");
      expect(store.tokenExpiry).toBe(0);
    });

    it("handles JWT with missing exp claim", async () => {
      const noExp = Buffer.from(JSON.stringify({ sub: 1 })).toString(
        "base64",
      );
      mockOfetch
        .mockResolvedValueOnce({
          access: `h.${noExp}.s`,
          refresh: "x",
        })
        .mockResolvedValueOnce({ pk: 1, username: "u", email: "e" });

      await store.login("u", "p");
      expect(store.tokenExpiry).toBe(0);
    });

    it("handles JWT with string exp claim", async () => {
      const strExp = Buffer.from(
        JSON.stringify({ exp: "not-a-number" }),
      ).toString("base64");
      mockOfetch
        .mockResolvedValueOnce({
          access: `h.${strExp}.s`,
          refresh: "x",
        })
        .mockResolvedValueOnce({ pk: 1, username: "u", email: "e" });

      await store.login("u", "p");
      expect(store.tokenExpiry).toBe(0);
    });
  });

  describe("refreshAccessToken edge cases", () => {
    it("refresh sends no body (cookie-based)", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 10;
      const expSeconds = Math.floor(Date.now() / 1000) + 300;
      mockOfetch.mockResolvedValueOnce({
        access: makeJwt(expSeconds),
      });

      await store.refreshAccessToken();

      const callArgs = mockOfetch.mock.calls[0];
      expect(callArgs[1].body).toBeUndefined();
    });

    it("handles refresh response with malformed JWT", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 10;
      mockOfetch.mockResolvedValueOnce({ access: "bad" });

      await store.refreshAccessToken();

      // parseJwtExpirySeconds returns 0 -> tokenExpiry = 0
      expect(store.tokenExpiry).toBe(0);
    });
  });

  describe("logout edge cases", () => {
    it("clears refresh timer on logout", async () => {
      const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout");
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 300;

      await store.logout();

      // clearTimeout should have been called during logout
      expect(store.tokenExpiry).toBeNull();
      expect(clearTimeoutSpy).toHaveBeenCalled();
      clearTimeoutSpy.mockRestore();
    });

    it("server logout error does not prevent local cleanup", async () => {
      store.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      store.user = { pk: 1, username: "u", email: "e" };
      mockOfetch.mockRejectedValueOnce(new Error("Network error"));

      await store.logout();

      expect(store.tokenExpiry).toBeNull();
      expect(store.user).toBeNull();
      expect(sessionStorage.removeItem).toHaveBeenCalledWith(
        "token_expiry",
      );
    });
  });
});

describe("calcRefreshDelay", () => {
  it("returns 0 when token is already expired", () => {
    const now = 1000000;
    const exp = now - 1000; // expired 1s ago
    expect(calcRefreshDelay(exp, now)).toBe(0);
  });

  it("returns 0 when remaining time is less than MIN_REFRESH_MARGIN_MS (30s)", () => {
    const now = 1000000;
    const exp = now + 20000; // expires in 20s
    expect(calcRefreshDelay(exp, now)).toBe(0);
  });

  it("returns 75% of remaining time for long-lived tokens", () => {
    const now = 0;
    const exp = 3600000; // expires in 1 hour
    expect(calcRefreshDelay(exp, now)).toBe(2700000);
  });

  it("returns margin-based delay when 75% exceeds remaining-minus-margin", () => {
    const now = 0;
    const exp = 60000; // expires in 60s
    expect(calcRefreshDelay(exp, now)).toBe(30000);
  });

  it("works correctly for default 300s token lifetime", () => {
    const now = 0;
    const exp = 300000; // 300s = 5 min
    expect(calcRefreshDelay(exp, now)).toBe(225000);
  });

  it("works correctly for 3600s token lifetime", () => {
    const now = 0;
    const exp = 3600000; // 1 hour
    expect(calcRefreshDelay(exp, now)).toBe(2700000);
  });

  it("returns 0 when expMs equals nowMs", () => {
    expect(calcRefreshDelay(5000, 5000)).toBe(0);
  });

  it("handles remaining time exactly at 30s margin boundary", () => {
    const now = 0;
    const exp = 30000; // exactly 30s remaining
    expect(calcRefreshDelay(exp, now)).toBe(0);
  });

  it("handles remaining time just above 30s margin boundary", () => {
    const now = 0;
    const exp = 31000; // 31s remaining
    expect(calcRefreshDelay(exp, now)).toBe(1000);
  });
});
