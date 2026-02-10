import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore, calcRefreshDelay } from "@/stores/auth";
import type { User } from "@/types";

// Mock ofetch
vi.mock("ofetch", () => ({
  ofetch: vi.fn(),
}));

describe("useAuthStore", () => {
  let store: ReturnType<typeof useAuthStore>;
  let mockOfetch: any;
  let sessionStorageMock: Record<string, string>;

  beforeEach(async () => {
    // Reset and setup ofetch mock first
    const { ofetch } = await import("ofetch");
    mockOfetch = ofetch as any;
    vi.clearAllMocks();

    // Default: resolve to undefined (prevents .catch() on undefined errors)
    mockOfetch.mockResolvedValue(undefined);

    // Setup Pinia - create fresh instance for each test
    setActivePinia(createPinia());

    // Create store after clearing mocks and setting up Pinia
    store = useAuthStore();

    // Ensure clean state
    store.accessToken = null;
    store.refreshToken = null;
    store.user = null;

    // Mock sessionStorage
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
  });

  describe("login", () => {
    it("should_store_tokens_in_state_and_sessionStorage_when_login_succeeds", async () => {
      // Arrange
      const mockTokens = {
        access: "mock-access-token",
        refresh: "mock-refresh-token",
      };
      const mockUser: User = {
        pk: 1,
        username: "testuser",
        email: "test@example.com",
      };

      mockOfetch
        .mockResolvedValueOnce(mockTokens) // login response
        .mockResolvedValueOnce(mockUser); // fetchUser response

      // Act
      await store.login("testuser", "password123");

      // Assert - verify ofetch calls
      expect(mockOfetch).toHaveBeenCalledTimes(2);
      expect(mockOfetch).toHaveBeenNthCalledWith(1, "/api/v1/auth/login/", {
        method: "POST",
        body: { username: "testuser", password: "password123" },
      });
      expect(mockOfetch).toHaveBeenNthCalledWith(2, "/api/v1/auth/user/", {
        headers: { Authorization: "Bearer mock-access-token" },
      });

      // Assert - verify state
      expect(store.accessToken).toBe("mock-access-token");
      expect(store.refreshToken).toBe("mock-refresh-token");
      expect(store.user).toEqual(mockUser);
      expect(store.isAuthenticated).toBe(true);

      // Assert - verify sessionStorage
      expect(sessionStorage.setItem).toHaveBeenCalledWith(
        "access_token",
        "mock-access-token"
      );
      expect(sessionStorage.setItem).toHaveBeenCalledWith(
        "refresh_token",
        "mock-refresh-token"
      );
    });

    it("should_throw_error_when_login_fails", async () => {
      // Arrange
      const loginError = new Error("Invalid credentials");
      mockOfetch.mockRejectedValueOnce(loginError);

      // Ensure store starts in clean state
      expect(store.accessToken).toBeNull();

      // Act & Assert
      await expect(store.login("testuser", "wrongpass")).rejects.toThrow(
        "Invalid credentials"
      );
      expect(store.accessToken).toBeNull();
      expect(store.refreshToken).toBeNull();
      expect(store.user).toBeNull();
      expect(store.isAuthenticated).toBe(false);
    });
  });

  describe("logout", () => {
    it("should_clear_tokens_and_user_from_state_and_sessionStorage", async () => {
      // Arrange - setup authenticated state
      store.accessToken = "access-token";
      store.refreshToken = "refresh-token";
      store.user = { pk: 1, username: "testuser", email: "test@example.com" };
      sessionStorageMock["access_token"] = "access-token";
      sessionStorageMock["refresh_token"] = "refresh-token";

      // Act
      await store.logout();

      // Assert - verify state cleared
      expect(store.accessToken).toBeNull();
      expect(store.refreshToken).toBeNull();
      expect(store.user).toBeNull();
      expect(store.isAuthenticated).toBe(false);

      // Assert - verify sessionStorage cleared
      expect(sessionStorage.removeItem).toHaveBeenCalledWith("access_token");
      expect(sessionStorage.removeItem).toHaveBeenCalledWith("refresh_token");
    });

    it("should_handle_logout_when_already_logged_out", async () => {
      // Arrange - ensure clean state
      expect(store.accessToken).toBeNull();

      // Act
      await store.logout();

      // Assert - should not throw and state remains null
      expect(store.accessToken).toBeNull();
      expect(store.refreshToken).toBeNull();
      expect(store.user).toBeNull();
    });
  });

  describe("fetchUser", () => {
    it("should_fetch_and_store_user_data_when_access_token_exists", async () => {
      // Arrange
      store.accessToken = "valid-access-token";
      const mockUser: User = {
        pk: 1,
        username: "testuser",
        email: "test@example.com",
      };
      mockOfetch.mockResolvedValueOnce(mockUser);

      // Act
      await store.fetchUser();

      // Assert
      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/user/", {
        headers: { Authorization: "Bearer valid-access-token" },
      });
      expect(store.user).toEqual(mockUser);
    });

    it("should_not_fetch_user_when_no_access_token", async () => {
      // Arrange
      store.accessToken = null;

      // Act
      await store.fetchUser();

      // Assert
      expect(mockOfetch).not.toHaveBeenCalled();
      expect(store.user).toBeNull();
    });

    it("should_logout_when_fetch_user_fails", async () => {
      // Arrange
      store.accessToken = "invalid-token";
      store.refreshToken = "some-refresh-token";
      store.user = { pk: 1, username: "testuser", email: "test@example.com" };
      mockOfetch.mockRejectedValueOnce(new Error("Unauthorized"));

      // Act
      await store.fetchUser();

      // Assert - verify logout was called
      expect(store.accessToken).toBeNull();
      expect(store.refreshToken).toBeNull();
      expect(store.user).toBeNull();
    });
  });

  describe("refreshAccessToken", () => {
    it("should_update_access_token_when_refresh_succeeds", async () => {
      // Arrange
      store.refreshToken = "valid-refresh-token";
      const mockResponse = { access: "new-access-token" };
      mockOfetch.mockResolvedValueOnce(mockResponse);

      // Act
      await store.refreshAccessToken();

      // Assert
      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/token/refresh/", {
        method: "POST",
        body: { refresh: "valid-refresh-token" },
        timeout: 5000,
      });
      expect(store.accessToken).toBe("new-access-token");
      expect(sessionStorage.setItem).toHaveBeenCalledWith(
        "access_token",
        "new-access-token"
      );
    });

    it("should_logout_when_refresh_token_is_missing", async () => {
      // Arrange
      store.refreshToken = null;
      store.accessToken = "some-token";

      // Act
      await store.refreshAccessToken();

      // Assert
      expect(mockOfetch).not.toHaveBeenCalled();
      expect(store.accessToken).toBeNull();
      expect(store.refreshToken).toBeNull();
    });

    it("should_logout_when_refresh_fails", async () => {
      // Arrange
      store.refreshToken = "expired-refresh-token";
      store.accessToken = "old-access-token";
      mockOfetch.mockRejectedValueOnce(new Error("Refresh token expired"));

      // Act
      await store.refreshAccessToken();

      // Assert
      expect(store.accessToken).toBeNull();
      expect(store.refreshToken).toBeNull();
      expect(sessionStorage.removeItem).toHaveBeenCalledWith("access_token");
      expect(sessionStorage.removeItem).toHaveBeenCalledWith("refresh_token");
    });
  });

  describe("isAuthenticated", () => {
    it("should_return_true_when_access_token_exists", () => {
      // Arrange
      store.accessToken = "some-token";

      // Assert
      expect(store.isAuthenticated).toBe(true);
    });

    it("should_return_false_when_access_token_is_null", () => {
      // Arrange
      store.accessToken = null;

      // Assert
      expect(store.isAuthenticated).toBe(false);
    });
  });

  describe("initialization", () => {
    it("should_load_tokens_from_sessionStorage_on_creation", () => {
      // Arrange
      sessionStorageMock["access_token"] = "stored-access-token";
      sessionStorageMock["refresh_token"] = "stored-refresh-token";

      // Act - create completely new Pinia instance and store
      setActivePinia(createPinia());
      const newStore = useAuthStore();

      // Assert - tokens should be loaded from sessionStorage
      expect(newStore.accessToken).toBe("stored-access-token");
      expect(newStore.refreshToken).toBe("stored-refresh-token");
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
    // 75% of 3600000 = 2700000ms, margin = 3600000-30000 = 3570000ms
    // min(2700000, 3570000) = 2700000
    expect(calcRefreshDelay(exp, now)).toBe(2700000);
  });

  it("returns margin-based delay when 75% exceeds remaining-minus-margin", () => {
    const now = 0;
    const exp = 60000; // expires in 60s
    // 75% of 60000 = 45000ms, margin = 60000-30000 = 30000ms
    // min(45000, 30000) = 30000
    expect(calcRefreshDelay(exp, now)).toBe(30000);
  });

  it("works correctly for default 300s token lifetime", () => {
    const now = 0;
    const exp = 300000; // 300s = 5 min
    // 75% of 300000 = 225000ms, margin = 300000-30000 = 270000ms
    // min(225000, 270000) = 225000 => refreshes after 225s (75s before expiry)
    expect(calcRefreshDelay(exp, now)).toBe(225000);
  });

  it("works correctly for 3600s token lifetime", () => {
    const now = 0;
    const exp = 3600000; // 1 hour
    // 75% of 3600000 = 2700000, margin = 3570000
    // min(2700000, 3570000) = 2700000 => refreshes after 45min (15min before expiry)
    expect(calcRefreshDelay(exp, now)).toBe(2700000);
  });

  it("returns 0 when expMs equals nowMs", () => {
    expect(calcRefreshDelay(5000, 5000)).toBe(0);
  });

  it("handles remaining time exactly at 30s margin boundary", () => {
    const now = 0;
    const exp = 30000; // exactly 30s remaining
    // margin = 30000-30000 = 0 => returns 0
    expect(calcRefreshDelay(exp, now)).toBe(0);
  });

  it("handles remaining time just above 30s margin boundary", () => {
    const now = 0;
    const exp = 31000; // 31s remaining
    // 75% of 31000 = 23250, margin = 31000-30000 = 1000
    // min(23250, 1000) = 1000
    expect(calcRefreshDelay(exp, now)).toBe(1000);
  });
});
