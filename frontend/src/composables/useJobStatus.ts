import { computed } from "vue";
import type { WsStatusUpdate, WsLogMessage } from "@/types";
import { useWebSocket } from "./useWebSocket";

export function useJobStatus(jobId: number) {
  const { messages, isConnected, connectionState, connect, disconnect } = useWebSocket<WsStatusUpdate>(
    `/ws/job/${jobId}/status/`
  );

  const latestStatus = computed(() => {
    if (messages.value.length === 0) return null;
    return messages.value[messages.value.length - 1];
  });

  return { messages, isConnected, connectionState, latestStatus, connect, disconnect };
}

export function useJobLogs(jobId: number) {
  const { messages, isConnected, connectionState, connect, disconnect } = useWebSocket<WsLogMessage>(
    `/ws/job/${jobId}/logs/`
  );

  return { logs: messages, isConnected, connectionState, connect, disconnect };
}
