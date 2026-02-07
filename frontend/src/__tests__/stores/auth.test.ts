import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "@/stores/auth";
import type { User } from "@/types";

// Mock ofetch
vi.mock("ofetch", () => ({
  ofetch: vi.fn(),
}));

describe("useAuthStore", () => {
  let store: ReturnType<typeof useAuthStore>;
  let mockOfetch: any;
  let localStorageMock: Record<string, string>;

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

    // Mock localStorage
    localStorageMock = {};
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) => localStorageMock[key] || null),
      setItem: vi.fn((key: string, value: string) => {
        localStorageMock[key] = value;
      }),
      removeItem: vi.fn((key: string) => {
        delete localStorageMock[key];
      }),
      clear: vi.fn(() => {
        localStorageMock = {};
      }),
    });
  });

  describe("login", () => {
    it("should_store_tokens_in_state_and_localStorage_when_login_succeeds", async () => {
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

      // Assert - verify localStorage
      expect(localStorage.setItem).toHaveBeenCalledWith(
        "access_token",
        "mock-access-token"
      );
      expect(localStorage.setItem).toHaveBeenCalledWith(
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
    it("should_clear_tokens_and_user_from_state_and_localStorage", async () => {
      // Arrange - setup authenticated state
      store.accessToken = "access-token";
      store.refreshToken = "refresh-token";
      store.user = { pk: 1, username: "testuser", email: "test@example.com" };
      localStorageMock["access_token"] = "access-token";
      localStorageMock["refresh_token"] = "refresh-token";

      // Act
      await store.logout();

      // Assert - verify state cleared
      expect(store.accessToken).toBeNull();
      expect(store.refreshToken).toBeNull();
      expect(store.user).toBeNull();
      expect(store.isAuthenticated).toBe(false);

      // Assert - verify localStorage cleared
      expect(localStorage.removeItem).toHaveBeenCalledWith("access_token");
      expect(localStorage.removeItem).toHaveBeenCalledWith("refresh_token");
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
      expect(localStorage.setItem).toHaveBeenCalledWith(
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
      expect(localStorage.removeItem).toHaveBeenCalledWith("access_token");
      expect(localStorage.removeItem).toHaveBeenCalledWith("refresh_token");
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
    it("should_load_tokens_from_localStorage_on_creation", () => {
      // Arrange
      localStorageMock["access_token"] = "stored-access-token";
      localStorageMock["refresh_token"] = "stored-refresh-token";

      // Act - create completely new Pinia instance and store
      setActivePinia(createPinia());
      const newStore = useAuthStore();

      // Assert - tokens should be loaded from localStorage
      expect(newStore.accessToken).toBe("stored-access-token");
      expect(newStore.refreshToken).toBe("stored-refresh-token");
    });
  });
});
