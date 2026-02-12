import { describe, it, expect, vi, beforeEach } from "vitest";

const apiMock = vi.fn();
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

vi.mock("@/api/endpoints", () => ({
  ENDPOINTS: {
    API_KEYS: "/api_keys/",
    API_KEY_DETAIL: (id: number) => `/api_keys/${id}/`,
    API_KEY_REVOKE: (id: number) => `/api_keys/${id}/revoke/`,
    RETRAINING_SCHEDULE: "/retraining_schedule/",
    RETRAINING_SCHEDULE_DETAIL: (id: number) => `/retraining_schedule/${id}/`,
    RETRAINING_SCHEDULE_TRIGGER: (id: number) => `/retraining_schedule/${id}/trigger/`,
    RETRAINING_RUN: "/retraining_run/",
    DEPLOYMENT_SLOT: "/deployment_slot/",
    DEPLOYMENT_SLOT_DETAIL: (id: number) => `/deployment_slot/${id}/`,
    AB_TEST: "/ab_test/",
    AB_TEST_DETAIL: (id: number) => `/ab_test/${id}/`,
    AB_TEST_START: (id: number) => `/ab_test/${id}/start/`,
    AB_TEST_STOP: (id: number) => `/ab_test/${id}/stop/`,
    AB_TEST_RESULTS: (id: number) => `/ab_test/${id}/results/`,
    AB_TEST_PROMOTE_WINNER: (id: number) => `/ab_test/${id}/promote_winner/`,
  },
}));

import {
  getApiKeys,
  createApiKey,
  revokeApiKey,
  deleteApiKey,
  getRetrainingSchedules,
  createRetrainingSchedule,
  updateRetrainingSchedule,
  triggerRetraining,
  getRetrainingRuns,
  getDeploymentSlots,
  createDeploymentSlot,
  updateDeploymentSlot,
  deleteDeploymentSlot,
  getABTests,
  createABTest,
  startABTest,
  stopABTest,
  getABTestResults,
  getABTestDetail,
  promoteWinner,
} from "@/api/production";

describe("production API", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.mockResolvedValue({});
  });

  // API Keys
  describe("API Keys", () => {
    it("getApiKeys calls api with correct args", async () => {
      const signal = new AbortController().signal;
      await getApiKeys(1, signal);
      expect(apiMock).toHaveBeenCalledWith("/api_keys/", { params: { project: 1 }, signal });
    });

    it("createApiKey calls api with POST", async () => {
      const data = { project: 1, name: "test", scopes: ["predict"] };
      await createApiKey(data);
      expect(apiMock).toHaveBeenCalledWith("/api_keys/", { method: "POST", body: data });
    });

    it("revokeApiKey calls api with POST", async () => {
      await revokeApiKey(5);
      expect(apiMock).toHaveBeenCalledWith("/api_keys/5/revoke/", { method: "POST" });
    });

    it("deleteApiKey calls api with DELETE", async () => {
      await deleteApiKey(5);
      expect(apiMock).toHaveBeenCalledWith("/api_keys/5/", { method: "DELETE" });
    });
  });

  // Retraining
  describe("Retraining", () => {
    it("getRetrainingSchedules calls api with correct args", async () => {
      const signal = new AbortController().signal;
      await getRetrainingSchedules(2, signal);
      expect(apiMock).toHaveBeenCalledWith("/retraining_schedule/", { params: { project: 2 }, signal });
    });

    it("createRetrainingSchedule calls api with POST", async () => {
      const data = { project: 1, cron_expression: "0 2 * * *" };
      await createRetrainingSchedule(data);
      expect(apiMock).toHaveBeenCalledWith("/retraining_schedule/", { method: "POST", body: data });
    });

    it("updateRetrainingSchedule calls api with PATCH", async () => {
      const data = { is_enabled: false };
      await updateRetrainingSchedule(3, data);
      expect(apiMock).toHaveBeenCalledWith("/retraining_schedule/3/", { method: "PATCH", body: data });
    });

    it("triggerRetraining calls api with POST", async () => {
      await triggerRetraining(3);
      expect(apiMock).toHaveBeenCalledWith("/retraining_schedule/3/trigger/", { method: "POST" });
    });

    it("getRetrainingRuns calls api with correct args", async () => {
      const signal = new AbortController().signal;
      await getRetrainingRuns(10, signal);
      expect(apiMock).toHaveBeenCalledWith("/retraining_run/", { params: { schedule: 10 }, signal });
    });
  });

  // Deployment Slots
  describe("Deployment Slots", () => {
    it("getDeploymentSlots calls api with correct args", async () => {
      const signal = new AbortController().signal;
      await getDeploymentSlots(1, signal);
      expect(apiMock).toHaveBeenCalledWith("/deployment_slot/", { params: { project: 1 }, signal });
    });

    it("createDeploymentSlot calls api with POST", async () => {
      const data = { project: 1, name: "Primary", trained_model: 5, weight: 50 };
      await createDeploymentSlot(data);
      expect(apiMock).toHaveBeenCalledWith("/deployment_slot/", { method: "POST", body: data });
    });

    it("updateDeploymentSlot calls api with PATCH", async () => {
      const data = { weight: 75 };
      await updateDeploymentSlot(2, data);
      expect(apiMock).toHaveBeenCalledWith("/deployment_slot/2/", { method: "PATCH", body: data });
    });

    it("deleteDeploymentSlot calls api with DELETE", async () => {
      await deleteDeploymentSlot(2);
      expect(apiMock).toHaveBeenCalledWith("/deployment_slot/2/", { method: "DELETE" });
    });
  });

  // A/B Tests
  describe("A/B Tests", () => {
    it("getABTests calls api with correct args", async () => {
      const signal = new AbortController().signal;
      await getABTests(1, signal);
      expect(apiMock).toHaveBeenCalledWith("/ab_test/", { params: { project: 1 }, signal });
    });

    it("createABTest calls api with POST", async () => {
      const data = { project: 1, name: "test" };
      await createABTest(data);
      expect(apiMock).toHaveBeenCalledWith("/ab_test/", { method: "POST", body: data });
    });

    it("startABTest calls api with POST", async () => {
      await startABTest(4);
      expect(apiMock).toHaveBeenCalledWith("/ab_test/4/start/", { method: "POST" });
    });

    it("stopABTest calls api with POST", async () => {
      await stopABTest(4);
      expect(apiMock).toHaveBeenCalledWith("/ab_test/4/stop/", { method: "POST" });
    });

    it("getABTestResults calls api with correct args", async () => {
      const signal = new AbortController().signal;
      await getABTestResults(4, signal);
      expect(apiMock).toHaveBeenCalledWith("/ab_test/4/results/", { signal });
    });

    it("getABTestDetail calls api with correct args", async () => {
      const signal = new AbortController().signal;
      await getABTestDetail(4, signal);
      expect(apiMock).toHaveBeenCalledWith("/ab_test/4/", { signal });
    });

    it("promoteWinner calls api with POST and slot_id", async () => {
      await promoteWinner(4, 7);
      expect(apiMock).toHaveBeenCalledWith("/ab_test/4/promote_winner/", { method: "POST", body: { slot_id: 7 } });
    });
  });
});
