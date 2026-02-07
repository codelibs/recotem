import { describe, it, expect, vi, beforeEach } from "vitest";
import { useJobLogs } from "@/composables/useJobStatus";

// Mock useWebSocket composable
vi.mock("@/composables/useWebSocket", () => ({
  useWebSocket: vi.fn((path: string) => {
    const messages = { value: [] };
    const isConnected = { value: false };
    const connect = vi.fn(() => {
      isConnected.value = true;
    });
    const disconnect = vi.fn(() => {
      isConnected.value = false;
    });

    return { messages, isConnected, connect, disconnect };
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
});
