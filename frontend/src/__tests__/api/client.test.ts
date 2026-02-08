import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "@/stores/auth";
import type { FetchOptions } from "ofetch";

// Mock ofetch module - use factory function to avoid hoisting issues
vi.mock("ofetch", () => {
  const mockOfetchCreate = vi.fn();
  const mockOfetch = vi.fn();

  class MockFetchError extends Error {
    response: any;
    data: any;
    constructor(message: string, options?: { response?: any; data?: any }) {
      super(message);
      this.name = "FetchError";
      this.response = options?.response;
      this.data = options?.data;
    }
  }

  return {
    ofetch: Object.assign(mockOfetch, {
      create: mockOfetchCreate,
    }),
    FetchError: MockFetchError,
  };
});

describe("api client", () => {
  let onRequestCallback: any;
  let onResponseErrorCallback: any;
  let authStore: ReturnType<typeof useAuthStore>;
  let mockOfetchCreate: any;
  let mockOfetch: any;

  beforeEach(async () => {
    // Setup Pinia
    setActivePinia(createPinia());
    authStore = useAuthStore();

    // Get mocks from the module
    const { ofetch } = await import("ofetch");
    mockOfetch = ofetch as any;
    mockOfetchCreate = (ofetch as any).create;

    // Mock localStorage
    const localStorageMock: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) => localStorageMock[key] || null),
      setItem: vi.fn((key: string, value: string) => {
        localStorageMock[key] = value;
      }),
      removeItem: vi.fn((key: string) => {
        delete localStorageMock[key];
      }),
    });

    // Mock sessionStorage (auth store uses sessionStorage for tokens)
    const sessionStorageMock: Record<string, string> = {};
    vi.stubGlobal("sessionStorage", {
      getItem: vi.fn((key: string) => sessionStorageMock[key] || null),
      setItem: vi.fn((key: string, value: string) => {
        sessionStorageMock[key] = value;
      }),
      removeItem: vi.fn((key: string) => {
        delete sessionStorageMock[key];
      }),
    });

    // Mock Headers in global scope
    vi.stubGlobal("Headers", class MockHeaders {
      private headers: Map<string, string> = new Map();

      constructor(init?: any) {
        if (init && typeof init === 'object') {
          Object.entries(init).forEach(([key, value]) => {
            this.headers.set(key.toLowerCase(), String(value));
          });
        }
      }

      get(name: string): string | null {
        return this.headers.get(name.toLowerCase()) || null;
      }

      set(name: string, value: string): void {
        this.headers.set(name.toLowerCase(), value);
      }

      has(name: string): boolean {
        return this.headers.has(name.toLowerCase());
      }
    });

    // Reset mocks
    vi.clearAllMocks();
    mockOfetchCreate.mockClear();
    mockOfetch.mockClear();

    // Default: resolve to undefined so auth store logout's server-side call works
    mockOfetch.mockResolvedValue(undefined);

    // Capture the callbacks passed to ofetch.create
    mockOfetchCreate.mockImplementation((config: any) => {
      onRequestCallback = config.onRequest;
      onResponseErrorCallback = config.onResponseError;
      return mockOfetch;
    });

    // Re-import the api client to get fresh instance
    vi.resetModules();
    await import("@/api/client");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("configuration", () => {
    it("should_create_ofetch_instance_with_correct_base_url", () => {
      expect(mockOfetchCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          baseURL: "/api/v1",
          headers: {
            "Content-Type": "application/json",
          },
        })
      );
    });
  });

  describe("onRequest - auth header injection", () => {
    it("should_add_authorization_header_when_access_token_exists", () => {
      // Arrange
      authStore.accessToken = "test-access-token";
      const options: FetchOptions = { headers: {} };

      // Act
      onRequestCallback({ options });

      // Assert
      const headers = options.headers as any;
      expect(headers.get("authorization")).toBe("Bearer test-access-token");
    });

    it("should_not_add_authorization_header_when_no_token", () => {
      // Arrange
      authStore.accessToken = null;
      const options: FetchOptions = { headers: {} };

      // Act
      onRequestCallback({ options });

      // Assert - headers should remain as plain object without Authorization
      const headers = options.headers as any;
      // When no token, headers stay as plain object (no Headers constructor called)
      expect(headers.authorization).toBeUndefined();
    });

    it("should_update_token_when_it_changes", () => {
      // Arrange
      authStore.accessToken = "old-token";
      const options1: FetchOptions = { headers: {} };
      onRequestCallback({ options: options1 });
      const headers1 = options1.headers as any;
      expect(headers1.get("authorization")).toBe("Bearer old-token");

      // Act - change token
      authStore.accessToken = "new-token";
      const options2: FetchOptions = { headers: {} };
      onRequestCallback({ options: options2 });

      // Assert
      const headers2 = options2.headers as any;
      expect(headers2.get("authorization")).toBe("Bearer new-token");
    });
  });

  describe("onResponseError - 401 handling", () => {
    it("should_trigger_token_refresh_and_retry_on_401", async () => {
      // Arrange
      authStore.refreshToken = "refresh-token";
      authStore.accessToken = "old-access-token";

      const mockRefreshResponse = { access: "new-access-token" };
      mockOfetch.mockResolvedValueOnce(mockRefreshResponse);
      mockOfetch.mockResolvedValueOnce({ data: "success" });

      const request = "/api/v1/projects/";
      const response = { status: 401 };
      const options: FetchOptions = { headers: {} };

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - refresh was called (with timeout from auth store)
      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/token/refresh/", {
        method: "POST",
        body: { refresh: "refresh-token" },
        timeout: 5000,
      });

      // Assert - original request was retried
      expect(mockOfetch).toHaveBeenCalledWith(
        request,
        expect.objectContaining({
          headers: expect.anything(),
        })
      );
    });

    it("should_logout_on_401_for_auth_endpoints", async () => {
      // Arrange
      authStore.accessToken = "token";
      authStore.refreshToken = "refresh";
      authStore.user = { pk: 1, username: "test", email: "test@example.com" };

      const request = "/api/v1/auth/login/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - logout was called (tokens cleared)
      expect(authStore.accessToken).toBeNull();
      expect(authStore.refreshToken).toBeNull();
      expect(authStore.user).toBeNull();

      // Assert - refresh was NOT called (only server-side logout call if any)
      const refreshCalls = mockOfetch.mock.calls.filter(
        (c: any[]) => String(c[0]).includes("token/refresh"),
      );
      expect(refreshCalls).toHaveLength(0);
    });

    it("should_logout_on_401_for_token_refresh_endpoint", async () => {
      // Arrange
      authStore.accessToken = "token";
      authStore.refreshToken = "refresh";

      const request = "/api/v1/auth/token/refresh/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert
      expect(authStore.accessToken).toBeNull();
      expect(authStore.refreshToken).toBeNull();

      // Assert - refresh was NOT called (only server-side logout call if any)
      const refreshCalls = mockOfetch.mock.calls.filter(
        (c: any[]) => String(c[0]).includes("token/refresh"),
      );
      expect(refreshCalls).toHaveLength(0);
    });

    it("should_logout_when_refresh_token_fails", async () => {
      // Arrange
      authStore.refreshToken = "invalid-refresh-token";
      authStore.accessToken = "old-token";
      authStore.user = { pk: 1, username: "test", email: "test@example.com" };

      mockOfetch.mockRejectedValueOnce(new Error("Refresh failed"));

      const request = "/api/v1/projects/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert
      expect(authStore.accessToken).toBeNull();
      expect(authStore.refreshToken).toBeNull();
      expect(authStore.user).toBeNull();
    });

    it("should_logout_when_refresh_succeeds_but_returns_no_token", async () => {
      // Arrange
      authStore.refreshToken = "refresh-token";
      authStore.accessToken = "old-token";

      mockOfetch.mockResolvedValueOnce({ access: null });

      const request = "/api/v1/projects/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert
      expect(authStore.accessToken).toBeNull();
      expect(authStore.refreshToken).toBeNull();
    });

    it("should_not_interfere_with_non_5xx_non_401_errors", async () => {
      // Arrange
      authStore.accessToken = "token";
      const request = "/api/v1/projects/";
      const response = { status: 403 };
      const options: FetchOptions = {};

      // Act
      const result = await onResponseErrorCallback({ request, response, options });

      // Assert - should return undefined (no special handling)
      expect(result).toBeUndefined();
      expect(mockOfetch).not.toHaveBeenCalled();
    });

    it("should_retry_on_5xx_errors_with_backoff", async () => {
      // Arrange
      authStore.accessToken = "token";
      const request = "/api/v1/projects/";
      const response = { status: 500 };
      const options: FetchOptions = {};
      mockOfetch.mockResolvedValueOnce({ data: "recovered" });

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - should retry the request
      expect(mockOfetch).toHaveBeenCalledWith(
        request,
        expect.objectContaining({
          _retryCount: 1,
        })
      );
    });

    it("should_handle_request_as_string", async () => {
      // Arrange
      authStore.refreshToken = "refresh-token";
      const request = "/auth/login/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - should logout (login endpoint)
      expect(authStore.accessToken).toBeNull();
    });

    it("should_handle_request_as_object_with_toString", async () => {
      // Arrange
      authStore.refreshToken = "refresh-token";
      const request = {
        toString: () => "/auth/token/refresh/",
      };
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - should logout (refresh endpoint)
      expect(authStore.accessToken).toBeNull();
    });

    it("should_include_new_token_in_retry_request", async () => {
      // Arrange
      authStore.refreshToken = "refresh-token";
      authStore.accessToken = "old-token";

      const mockRefreshResponse = { access: "shiny-new-token" };
      mockOfetch.mockResolvedValueOnce(mockRefreshResponse);
      mockOfetch.mockResolvedValueOnce({ data: "success" });

      const request = "/api/v1/projects/";
      const response = { status: 401 };
      const options: FetchOptions = { headers: {} };

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - check the retry call has the new token
      expect(mockOfetch).toHaveBeenCalledTimes(2);
      const retryCall = mockOfetch.mock.calls[1];
      expect(retryCall[0]).toBe(request);

      const retryOptions = retryCall[1] as any;
      expect(retryOptions.headers.get("authorization")).toBe("Bearer shiny-new-token");
    });
  });

  describe("request flow integration", () => {
    it("should_add_auth_header_and_handle_401_in_sequence", async () => {
      // Arrange
      authStore.accessToken = "expired-token";
      authStore.refreshToken = "valid-refresh";

      const mockRefreshResponse = { access: "fresh-token" };
      const mockDataResponse = { id: 1, name: "Project" };

      mockOfetch.mockResolvedValueOnce(mockRefreshResponse);
      mockOfetch.mockResolvedValueOnce(mockDataResponse);

      const options: FetchOptions = { headers: {} };
      onRequestCallback({ options });

      // Verify initial auth header
      let headers = options.headers as any;
      expect(headers.get("authorization")).toBe("Bearer expired-token");

      // Simulate 401 response
      const request = "/api/v1/projects/";
      const response = { status: 401 };

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - token was refreshed and stored
      expect(authStore.accessToken).toBe("fresh-token");
    });
  });
});


describe("classifyApiError", () => {
  let classifyApiError: any;
  let MockFetchError: any;

  beforeEach(async () => {
    setActivePinia(createPinia());
    vi.resetModules();
    const clientModule = await import("@/api/client");
    classifyApiError = clientModule.classifyApiError;
    const ofetchModule = await import("ofetch");
    MockFetchError = (ofetchModule as any).FetchError;
  });

  it("classifies 400 as validation error", () => {
    const err = new MockFetchError("Bad request", {
      response: { status: 400 },
      data: { name: ["This field is required."] },
    });
    const result = classifyApiError(err);
    expect(result.kind).toBe("validation");
    expect(result.status).toBe(400);
    expect(result.fieldErrors).toEqual({ name: ["This field is required."] });
  });

  it("classifies 401 as unauthorized", () => {
    const err = new MockFetchError("Unauthorized", {
      response: { status: 401 },
      data: { detail: "Token expired." },
    });
    const result = classifyApiError(err);
    expect(result.kind).toBe("unauthorized");
    expect(result.message).toBe("Token expired.");
  });

  it("classifies 403 as forbidden", () => {
    const err = new MockFetchError("Forbidden", {
      response: { status: 403 },
      data: { detail: "Permission denied." },
    });
    const result = classifyApiError(err);
    expect(result.kind).toBe("forbidden");
  });

  it("classifies 404 as not_found", () => {
    const err = new MockFetchError("Not found", {
      response: { status: 404 },
      data: { detail: "Not found." },
    });
    const result = classifyApiError(err);
    expect(result.kind).toBe("not_found");
  });

  it("classifies 429 as rate_limited", () => {
    const err = new MockFetchError("Too many requests", {
      response: { status: 429 },
      data: { detail: "Request was throttled." },
    });
    const result = classifyApiError(err);
    expect(result.kind).toBe("rate_limited");
  });

  it("classifies 500 as server_error", () => {
    const err = new MockFetchError("Server error", {
      response: { status: 500 },
      data: {},
    });
    const result = classifyApiError(err);
    expect(result.kind).toBe("server_error");
  });

  it("classifies network error (no response)", () => {
    const err = new MockFetchError("fetch failed", {});
    const result = classifyApiError(err);
    expect(result.kind).toBe("network_error");
  });

  it("classifies timeout error", () => {
    const err = new MockFetchError("timeout", {});
    const result = classifyApiError(err);
    expect(result.kind).toBe("timeout");
  });

  it("classifies unknown error types", () => {
    const result = classifyApiError(new Error("unexpected"));
    expect(result.kind).toBe("unknown");
    expect(result.message).toBe("unexpected");
  });

  it("classifies non-Error values", () => {
    const result = classifyApiError("string error");
    expect(result.kind).toBe("unknown");
  });
});

describe("unwrapResults", () => {
  it("returns array as-is", async () => {
    // Dynamic import to avoid module mock interference
    const { unwrapResults } = await import("@/api/client");
    const arr = [{ id: 1 }, { id: 2 }];
    expect(unwrapResults(arr)).toBe(arr);
  });

  it("extracts results from paginated response", async () => {
    const { unwrapResults } = await import("@/api/client");
    const items = [{ id: 1 }, { id: 2 }];
    const paginated = { count: 2, next: null, previous: null, results: items };
    expect(unwrapResults(paginated)).toBe(items);
  });
});
