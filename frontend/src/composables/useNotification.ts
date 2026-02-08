import { useToast } from "primevue/usetoast";

export function useNotification() {
  const toast = useToast();

  function success(message: string, life = 5000) {
    toast.add({ severity: "success", summary: message, life });
  }
  function error(message: string, life = 10000) {
    toast.add({ severity: "error", summary: message, life });
  }
  function warning(message: string, life = 5000) {
    toast.add({ severity: "warn", summary: message, life });
  }
  function info(message: string, life = 5000) {
    toast.add({ severity: "info", summary: message, life });
  }

  return { success, error, warning, info };
}
