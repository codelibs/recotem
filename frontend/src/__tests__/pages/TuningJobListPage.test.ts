import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import TuningJobListPage from "@/pages/TuningJobListPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

const apiMock = vi.fn();
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  classifyApiError: (err: any) => ({
    kind: "unknown",
    status: undefined,
    message: err?.message ?? "Unknown error",
    fieldErrors: undefined,
  }),
  unwrapResults: (res: any) => (Array.isArray(res) ? res : res?.results ?? []),
}));

const baseStubs = {
  Button: {
    template:
      '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
    inheritAttrs: false,
  },
  Message: { template: '<div class="message"><slot /></div>' },
  Skeleton: { template: '<div class="skeleton" />' },
  DataTable: {
    template: '<div class="data-table"><slot /></div>',
    props: ["value", "loading"],
  },
  Column: { template: "<div />" },
  Tag: {
    template: '<span class="tag" :data-severity="severity">{{ value }}</span>',
    props: ["severity", "value"],
  },
  Select: {
    template: "<select />",
    props: ["modelValue", "options"],
  },
  Dialog: {
    template:
      '<div class="dialog" v-if="visible"><slot /></div>',
    props: ["visible", "header"],
  },
  EmptyState: {
    template:
      '<div class="empty-state">{{ title }}<slot /></div>',
    props: ["icon", "title", "description"],
  },
  ConfirmDialog: { template: "<div />" },
  RouterLink: { template: "<a><slot /></a>" },
};

function mountPage(overrideStubs: Record<string, any> = {}) {
  return mount(TuningJobListPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: { ...baseStubs, ...overrideStubs },
    },
  });
}

describe("TuningJobListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders loading skeletons", async () => {
    apiMock.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders data table on success", async () => {
    apiMock.mockResolvedValue({
      results: [
        {
          id: 1,
          status: "COMPLETED",
          best_score: 0.85,
          n_trials: 10,
          ins_datetime: "2025-01-01T00:00:00Z",
          best_config: 1,
        },
      ],
    });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
    expect(wrapper.find(".empty-state").exists()).toBe(false);
  });

  it("renders empty state when no jobs", async () => {
    apiMock.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No tuning jobs yet.");
  });

  it("renders error with retry on failure", async () => {
    apiMock.mockRejectedValueOnce(new Error("Connection refused"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Connection refused");
    expect(wrapper.text()).toContain("Retry");
  });

  it("retries fetch on retry button click", async () => {
    apiMock
      .mockRejectedValueOnce(new Error("Connection refused"))
      .mockResolvedValueOnce({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    const retryBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Retry"));
    expect(retryBtn).toBeDefined();
    await retryBtn!.trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledTimes(2);
  });

  it("ignores AbortError", async () => {
    const abortError = new DOMException("Aborted", "AbortError");
    apiMock.mockRejectedValueOnce(abortError);
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  describe("statusLabel and statusSeverity", () => {
    it("renders correct status labels for different job statuses", async () => {
      apiMock.mockResolvedValue({
        results: [
          { id: 1, status: "RUNNING", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
          { id: 2, status: "COMPLETED", best_score: 0.85, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: 1 },
          { id: 3, status: "FAILED", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
          { id: 4, status: "PENDING", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
        ],
      });
      // Use real DataTable/Column/Tag to exercise statusLabel/statusSeverity
      const wrapper = mountPage({
        DataTable: false,
        Column: false,
        Tag: false,
      });
      await flushPromises();

      const text = wrapper.text();
      expect(text).toContain("Running");
      expect(text).toContain("Completed");
      expect(text).toContain("Failed");
      expect(text).toContain("Pending");
    });

    it("renders correct severity tags for different statuses", async () => {
      apiMock.mockResolvedValue({
        results: [
          { id: 1, status: "RUNNING", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
          { id: 2, status: "COMPLETED", best_score: 0.85, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: 1 },
          { id: 3, status: "FAILED", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
          { id: 4, status: "PENDING", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
        ],
      });
      // Use real DataTable/Column/Tag to exercise statusSeverity
      const wrapper = mountPage({
        DataTable: false,
        Column: false,
        Tag: false,
      });
      await flushPromises();

      // PrimeVue Tag renders with p-tag-<severity> CSS class
      const html = wrapper.html();
      expect(html).toContain("info");
      expect(html).toContain("success");
      expect(html).toContain("danger");
      // "secondary" is the default for PENDING
      expect(html).toContain("secondary");
    });
  });

  describe("polling", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("starts polling on mount when active jobs exist", async () => {
      apiMock.mockResolvedValue({
        results: [
          { id: 1, status: "RUNNING", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
        ],
      });

      const wrapper = mountPage();
      await flushPromises();

      // Initial fetch call count
      expect(apiMock).toHaveBeenCalledTimes(1);

      // Advance timer by 15 seconds to trigger polling interval
      await vi.advanceTimersByTimeAsync(15000);
      await flushPromises();

      // A second API call should have been made by the polling interval
      expect(apiMock).toHaveBeenCalledTimes(2);

      wrapper.unmount();
    });

    it("stops polling on unmount", async () => {
      apiMock.mockResolvedValue({
        results: [
          { id: 1, status: "RUNNING", best_score: null, n_trials: 10, ins_datetime: "2025-01-01T00:00:00Z", best_config: null },
        ],
      });

      const wrapper = mountPage();
      await flushPromises();

      expect(apiMock).toHaveBeenCalledTimes(1);

      // Unmount to trigger stopPolling via onUnmounted
      wrapper.unmount();

      // Advance timer — no additional calls should be made
      await vi.advanceTimersByTimeAsync(30000);
      await flushPromises();

      // Still only the initial call
      expect(apiMock).toHaveBeenCalledTimes(1);
    });
  });
});
