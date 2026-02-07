import { computed } from "vue";
import { useWebSocket } from "./useWebSocket";

export function useJobStatus(jobId: number) {
  const { messages, isConnected, connect, disconnect } = useWebSocket(
    `/ws/job/${jobId}/status/`
  );

  const latestStatus = computed(() => {
    if (messages.value.length === 0) return null;
    return messages.value[messages.value.length - 1];
  });

  return { messages, isConnected, latestStatus, connect, disconnect };
}

export function useJobLogs(jobId: number) {
  const { messages, isConnected, connect, disconnect } = useWebSocket(
    `/ws/job/${jobId}/logs/`
  );

  return { logs: messages, isConnected, connect, disconnect };
}
