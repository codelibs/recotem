import { describe, it, expect, vi, beforeEach } from "vitest";
import { useJobLogs, useJobStatus } from "@/composables/useJobStatus";

// Mock useWebSocket composable
vi.mock("@/composables/useWebSocket", () => ({
  useWebSocket: vi.fn((path: string) => {
    const messages = { value: [] as any[] };
    const isConnected = { value: false };
    const connectionState = { value: "disconnected" };
    const connect = vi.fn(() => {
      isConnected.value = true;
      connectionState.value = "connected";
    });
    const disconnect = vi.fn(() => {
      isConnected.value = false;
      connectionState.value = "disconnected";
    });

    return { messages, isConnected, connectionState, connect, disconnect };
  }),
}));

describe("useJobStatus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("useJobLogs", () => {
    it("returns logs, isConnected, connect, and disconnect functions", () => {
      const jobId = 123;
      const result = useJobLogs(jobId);

      expect(result).toHaveProperty("logs");
      expect(result).toHaveProperty("isConnected");
      expect(result).toHaveProperty("connect");
      expect(result).toHaveProperty("disconnect");
    });

    it("connect() establishes WebSocket connection", () => {
      const jobId = 123;
      const { connect, isConnected } = useJobLogs(jobId);

      expect(isConnected.value).toBe(false);
      connect();
      expect(isConnected.value).toBe(true);
    });

    it("disconnect() closes WebSocket connection", () => {
      const jobId = 123;
      const { connect, disconnect, isConnected } = useJobLogs(jobId);

      connect();
      expect(isConnected.value).toBe(true);

      disconnect();
      expect(isConnected.value).toBe(false);
    });

    it("uses correct WebSocket path", async () => {
      const { useWebSocket } = await import("@/composables/useWebSocket");
      const jobId = 456;
      useJobLogs(jobId);

      expect(useWebSocket).toHaveBeenCalledWith(`/ws/job/${jobId}/logs/`);
    });
  });

  describe("useJobStatus", () => {
    it("returns messages, isConnected, connectionState, latestStatus, connect, disconnect", () => {
      const jobId = 123;
      const result = useJobStatus(jobId);

      expect(result).toHaveProperty("messages");
      expect(result).toHaveProperty("isConnected");
      expect(result).toHaveProperty("connectionState");
      expect(result).toHaveProperty("latestStatus");
      expect(result).toHaveProperty("connect");
      expect(result).toHaveProperty("disconnect");
    });

    it("latestStatus is null when no messages", () => {
      const jobId = 123;
      const { latestStatus } = useJobStatus(jobId);

      expect(latestStatus.value).toBeNull();
    });

    it("latestStatus returns the last message when messages exist", () => {
      const jobId = 123;
      const { messages, latestStatus } = useJobStatus(jobId);

      (messages.value as any[]).push({ status: "RUNNING", progress: 50 });
      (messages.value as any[]).push({ status: "COMPLETED", progress: 100 });

      expect(latestStatus.value).toEqual({ status: "COMPLETED", progress: 100 });
    });

    it("uses correct WebSocket path", async () => {
      const { useWebSocket } = await import("@/composables/useWebSocket");
      const jobId = 789;
      useJobStatus(jobId);

      expect(useWebSocket).toHaveBeenCalledWith(`/ws/job/${jobId}/status/`);
    });

    it("connect/disconnect work as expected", () => {
      const jobId = 123;
      const { connect, disconnect, isConnected, connectionState } = useJobStatus(jobId);

      expect(isConnected.value).toBe(false);
      expect(connectionState.value).toBe("disconnected");

      connect();
      expect(isConnected.value).toBe(true);
      expect(connectionState.value).toBe("connected");

      disconnect();
      expect(isConnected.value).toBe(false);
      expect(connectionState.value).toBe("disconnected");
    });
  });
});
