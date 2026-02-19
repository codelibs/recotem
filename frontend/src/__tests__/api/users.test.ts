import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock modules before importing the module under test
const mockApi = vi.fn();
vi.mock("@/api/client", () => ({ api: (...args: unknown[]) => mockApi(...args) }));
vi.mock("@/api/endpoints", () => ({
  ENDPOINTS: {
    USERS: "/users/",
    USER_DETAIL: (id: number) => `/users/${id}/`,
    USER_DEACTIVATE: (id: number) => `/users/${id}/deactivate/`,
    USER_ACTIVATE: (id: number) => `/users/${id}/activate/`,
    USER_RESET_PASSWORD: (id: number) => `/users/${id}/reset_password/`,
    USER_CHANGE_PASSWORD: "/users/change_password/",
  },
}));

import {
  getUsers,
  createUser,
  updateUser,
  deactivateUser,
  activateUser,
  resetUserPassword,
  changeOwnPassword,
} from "@/api/users";
import type { ManagedUser, UserCreatePayload, UserUpdatePayload } from "@/types";

const mockUser: ManagedUser = {
  id: 1,
  username: "alice",
  email: "alice@example.com",
  is_staff: false,
  is_active: true,
  date_joined: "2025-01-01T00:00:00Z",
  last_login: null,
};

describe("api/users", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("getUsers", () => {
    it("calls api with USERS endpoint", async () => {
      mockApi.mockResolvedValueOnce([mockUser]);
      const signal = new AbortController().signal;
      await getUsers(signal);
      expect(mockApi).toHaveBeenCalledWith("/users/", { signal });
    });

    it("calls api without signal when omitted", async () => {
      mockApi.mockResolvedValueOnce([mockUser]);
      await getUsers();
      expect(mockApi).toHaveBeenCalledWith("/users/", { signal: undefined });
    });

    it("returns the api response", async () => {
      mockApi.mockResolvedValueOnce([mockUser]);
      const result = await getUsers();
      expect(result).toEqual([mockUser]);
    });

    it("propagates errors from api", async () => {
      mockApi.mockRejectedValueOnce(new Error("Network error"));
      await expect(getUsers()).rejects.toThrow("Network error");
    });
  });

  describe("createUser", () => {
    const payload: UserCreatePayload = {
      username: "bob",
      email: "bob@example.com",
      password: "secret",
      is_staff: false,
    };

    it("calls api with POST method and body", async () => {
      mockApi.mockResolvedValueOnce({ ...mockUser, id: 2 });
      await createUser(payload);
      expect(mockApi).toHaveBeenCalledWith("/users/", { method: "POST", body: payload });
    });

    it("returns created user", async () => {
      const created = { ...mockUser, id: 2 };
      mockApi.mockResolvedValueOnce(created);
      const result = await createUser(payload);
      expect(result).toEqual(created);
    });
  });

  describe("updateUser", () => {
    const patch: UserUpdatePayload = { email: "new@example.com" };

    it("calls api with PATCH method and correct URL", async () => {
      mockApi.mockResolvedValueOnce({ ...mockUser, email: "new@example.com" });
      await updateUser(1, patch);
      expect(mockApi).toHaveBeenCalledWith("/users/1/", { method: "PATCH", body: patch });
    });

    it("returns updated user", async () => {
      const updated = { ...mockUser, email: "new@example.com" };
      mockApi.mockResolvedValueOnce(updated);
      const result = await updateUser(1, patch);
      expect(result).toEqual(updated);
    });
  });

  describe("deactivateUser", () => {
    it("calls api with POST to deactivate endpoint", async () => {
      mockApi.mockResolvedValueOnce({ ...mockUser, is_active: false });
      await deactivateUser(1);
      expect(mockApi).toHaveBeenCalledWith("/users/1/deactivate/", { method: "POST" });
    });
  });

  describe("activateUser", () => {
    it("calls api with POST to activate endpoint", async () => {
      mockApi.mockResolvedValueOnce({ ...mockUser, is_active: true });
      await activateUser(1);
      expect(mockApi).toHaveBeenCalledWith("/users/1/activate/", { method: "POST" });
    });
  });

  describe("resetUserPassword", () => {
    it("calls api with POST and new_password in body", async () => {
      mockApi.mockResolvedValueOnce(undefined);
      await resetUserPassword(1, "newpass123");
      expect(mockApi).toHaveBeenCalledWith("/users/1/reset_password/", {
        method: "POST",
        body: { new_password: "newpass123" },
      });
    });
  });

  describe("changeOwnPassword", () => {
    it("calls api with POST and both passwords", async () => {
      mockApi.mockResolvedValueOnce(undefined);
      await changeOwnPassword("oldpass", "newpass");
      expect(mockApi).toHaveBeenCalledWith("/users/change_password/", {
        method: "POST",
        body: { old_password: "oldpass", new_password: "newpass" },
      });
    });
  });
});
