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

function makeJwt(expSeconds: number): string {
  const payload = Buffer.from(JSON.stringify({ exp: expSeconds })).toString("base64");
  return `h.${payload}.s`;
}

describe("api client", () => {
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

    // Mock sessionStorage (auth store uses sessionStorage for expiry)
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

    // Reset mocks
    vi.clearAllMocks();
    mockOfetchCreate.mockClear();
    mockOfetch.mockClear();

    // Default: resolve to undefined so auth store logout's server-side call works
    mockOfetch.mockResolvedValue(undefined);

    // Capture the callbacks passed to ofetch.create
    mockOfetchCreate.mockImplementation((config: any) => {
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

    it("does not include Authorization header in default config", () => {
      const config = mockOfetchCreate.mock.calls[0][0];
      expect(config.headers).not.toHaveProperty("Authorization");
    });

    it("does not include Bearer token in default headers", () => {
      const config = mockOfetchCreate.mock.calls[0][0];
      const headerValues = Object.values(config.headers || {});
      const hasBearerHeader = headerValues.some(
        (v: any) => typeof v === "string" && v.startsWith("Bearer "),
      );
      expect(hasBearerHeader).toBe(false);
    });
  });

  describe("onResponseError - 401 handling", () => {
    it("should_trigger_token_refresh_and_retry_on_401", async () => {
      // Arrange
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      const expSeconds = Math.floor(Date.now() / 1000) + 300;
      const mockRefreshResponse = { access: makeJwt(expSeconds) };
      mockOfetch.mockResolvedValueOnce(mockRefreshResponse);
      mockOfetch.mockResolvedValueOnce({ data: "success" });

      const request = "/api/v1/projects/";
      const response = { status: 401 };
      const options: FetchOptions = { headers: {} };

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - refresh was called
      expect(mockOfetch).toHaveBeenCalledWith("/api/v1/auth/token/refresh/", {
        method: "POST",
        timeout: 5000,
      });

      // Assert - original request was retried
      expect(mockOfetch).toHaveBeenCalledWith(
        request,
        expect.objectContaining({})
      );
    });

    it("should_logout_on_401_for_auth_endpoints", async () => {
      // Arrange
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;
      authStore.user = { pk: 1, username: "test", email: "test@example.com" };

      const request = "/api/v1/auth/login/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - logout was called
      expect(authStore.tokenExpiry).toBeNull();
      expect(authStore.user).toBeNull();

      const refreshCalls = mockOfetch.mock.calls.filter(
        (c: any[]) => String(c[0]).includes("token/refresh"),
      );
      expect(refreshCalls).toHaveLength(0);
    });

    it("should_logout_on_401_for_token_refresh_endpoint", async () => {
      // Arrange
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      const request = "/api/v1/auth/token/refresh/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert
      expect(authStore.tokenExpiry).toBeNull();

      const refreshCalls = mockOfetch.mock.calls.filter(
        (c: any[]) => String(c[0]).includes("token/refresh"),
      );
      expect(refreshCalls).toHaveLength(0);
    });

    it("should_logout_when_refresh_token_fails", async () => {
      // Arrange
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;
      authStore.user = { pk: 1, username: "test", email: "test@example.com" };

      mockOfetch.mockRejectedValueOnce(new Error("Refresh failed"));

      const request = "/api/v1/projects/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert
      expect(authStore.tokenExpiry).toBeNull();
      expect(authStore.user).toBeNull();
    });

    it("should_logout_when_refresh_succeeds_but_returns_no_token", async () => {
      // Arrange
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      mockOfetch.mockResolvedValueOnce({ access: null });

      const request = "/api/v1/projects/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert
      expect(authStore.tokenExpiry).toBeNull();
    });

    it("should_not_interfere_with_non_5xx_non_401_errors", async () => {
      // Arrange
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
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;
      const request = "/auth/login/";
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - should logout (login endpoint)
      expect(authStore.tokenExpiry).toBeNull();
    });

    it("should_handle_request_as_object_with_toString", async () => {
      // Arrange
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;
      const request = {
        toString: () => "/auth/token/refresh/",
      };
      const response = { status: 401 };
      const options: FetchOptions = {};

      // Act
      await onResponseErrorCallback({ request, response, options });

      // Assert - should logout (refresh endpoint)
      expect(authStore.tokenExpiry).toBeNull();
    });
  });

  describe("onResponseError - 5xx retry behaviour", () => {
    it("does not retry POST requests on 500", async () => {
      const request = "/api/v1/projects/";
      const response = { status: 500 };
      const options: FetchOptions = { method: "POST" };

      await onResponseErrorCallback({ request, response, options });
      expect(mockOfetch).not.toHaveBeenCalled();
    });

    it("does not retry PUT requests on 500", async () => {
      const request = "/api/v1/projects/1/";
      const response = { status: 500 };
      const options: FetchOptions = { method: "PUT" };

      await onResponseErrorCallback({ request, response, options });
      expect(mockOfetch).not.toHaveBeenCalled();
    });

    it("retries GET requests on 502", async () => {
      const request = "/api/v1/projects/";
      const response = { status: 502 };
      const options: FetchOptions = {};
      mockOfetch.mockResolvedValueOnce({ data: "ok" });

      await onResponseErrorCallback({ request, response, options });

      expect(mockOfetch).toHaveBeenCalledWith(
        request,
        expect.objectContaining({ _retryCount: 1 }),
      );
    });

    it("stops retrying after MAX_RETRIES (3)", async () => {
      const request = "/api/v1/projects/";
      const response = { status: 500 };
      const options: FetchOptions = { _retryCount: 3 } as any;

      const result = await onResponseErrorCallback({
        request,
        response,
        options,
      });

      expect(result).toBeUndefined();
      expect(mockOfetch).not.toHaveBeenCalled();
    });

    it("stops retrying when total elapsed exceeds 5s", async () => {
      const request = "/api/v1/projects/";
      const response = { status: 500 };
      // Already 4s elapsed, next backoff 2s -> total 6s > 5s
      const options: FetchOptions = {
        _retryCount: 1,
        _retryElapsed: 4000,
      } as any;

      const result = await onResponseErrorCallback({
        request,
        response,
        options,
      });

      expect(result).toBeUndefined();
      expect(mockOfetch).not.toHaveBeenCalled();
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
