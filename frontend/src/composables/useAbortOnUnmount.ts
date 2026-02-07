import { getCurrentInstance, onUnmounted } from "vue";

/**
 * Returns an AbortController whose signal is automatically aborted
 * when the enclosing component unmounts. Pass the signal to fetch/ofetch
 * options to cancel in-flight requests on navigation.
 *
 * Usage:
 *   const { signal } = useAbortOnUnmount();
 *   const res = await api("/endpoint", { signal });
 */
export function useAbortOnUnmount(): AbortController {
  const controller = new AbortController();

  if (getCurrentInstance()) {
    onUnmounted(() => {
      controller.abort();
    });
  }

  return controller;
}
