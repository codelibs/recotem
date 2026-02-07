import { useNotificationStore } from "@/stores/notification";

export function useNotification() {
  return useNotificationStore();
}
