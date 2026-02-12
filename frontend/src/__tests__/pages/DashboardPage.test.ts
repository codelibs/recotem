import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { computed, nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import DashboardPage from "@/pages/DashboardPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

const apiMock = vi.fn();
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  classifyApiError: (err: any) => ({ kind: "unknown", status: undefined, message: err?.message ?? "Unknown error", fieldErrors: undefined }),
  unwrapResults: (res: any) => (Array.isArray(res) ? res : res.results),
}));

vi.mock("@/api/production", () => ({
  getRetrainingSchedules: vi.fn().mockResolvedValue({ results: [] }),
  getDeploymentSlots: vi.fn().mockResolvedValue({ results: [] }),
  getABTests: vi.fn().mockResolvedValue({ results: [] }),
}));

function mountPage() {
  return mount(DashboardPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      provide: {
        currentProject: computed(() => null),
      },
      stubs: {
        Button: { template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        StatCard: { template: '<div class="stat-card" />' },
        EmptyState: { template: '<div class="empty-state">{{ title }} {{ description }}<slot /></div>', props: ["icon", "title", "description"] },
      },
    },
  });
}

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders loading state initially", async () => {
    apiMock.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    // onMounted triggers loading=true; wait for the mounted hook to run
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders summary cards on success", async () => {
    apiMock.mockResolvedValue({
      n_data: 3,
      n_complete_jobs: 5,
      n_models: 2,
      ins_datetime: "2025-01-01T00:00:00Z",
    });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".stat-card").length).toBe(3);
  });

  it("renders error with retry on failure", async () => {
    apiMock.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Network error");
    expect(wrapper.text()).toContain("Retry");
  });

  it("retries on retry button click", async () => {
    apiMock
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({
        n_data: 1,
        n_complete_jobs: 0,
        n_models: 0,
        ins_datetime: "2025-01-01T00:00:00Z",
      });

    const wrapper = mountPage();
    await flushPromises();

    // Click retry
    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    expect(retryBtn).toBeDefined();
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(apiMock).toHaveBeenCalledTimes(2);
    expect(wrapper.findAll(".stat-card").length).toBe(3);
  });

  it("ignores AbortError", async () => {
    const abortError = new DOMException("Aborted", "AbortError");
    apiMock.mockRejectedValueOnce(abortError);
    const wrapper = mountPage();
    await flushPromises();
    // Should not show error message for abort
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("renders dashboard title", async () => {
    apiMock.mockResolvedValue({
      n_data: 0,
      n_complete_jobs: 0,
      n_models: 0,
    });

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find("h1").exists()).toBe(true);
    expect(wrapper.find("h1").text()).toContain("Dashboard");
  });

  it("shows pipeline progress steps", async () => {
    apiMock.mockResolvedValue({
      n_data: 5,
      n_complete_jobs: 3,
      n_models: 2,
    });

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("Pipeline Progress");
    expect(wrapper.text()).toContain("1. Data");
    expect(wrapper.text()).toContain("2. Tuning");
    expect(wrapper.text()).toContain("3. Model");
  });

  it("shows 'Get started' when no data", async () => {
    apiMock.mockResolvedValue(null);

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("Get started");
    expect(wrapper.text()).toContain("Upload training data, then start tuning.");
  });

  // --- Error scenario tests ---

  it("displays error severity message from classifyApiError", async () => {
    apiMock.mockRejectedValueOnce(new Error("Server is down"));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Server is down");
  });

  it("hides skeleton loaders after error response", async () => {
    apiMock.mockRejectedValueOnce(new Error("Request failed"));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("shows skeleton loaders during loading and hides on success", async () => {
    let resolveApi: (value: unknown) => void;
    apiMock.mockReturnValue(new Promise((resolve) => { resolveApi = resolve; }));

    const wrapper = mountPage();
    await nextTick();

    // Loading state: skeletons visible
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
    expect(wrapper.findAll(".stat-card").length).toBe(0);

    // Resolve the API call
    resolveApi!({
      n_data: 2,
      n_complete_jobs: 1,
      n_models: 1,
      ins_datetime: "2025-01-01T00:00:00Z",
    });
    await flushPromises();

    // Loaded state: stat cards visible, no skeletons
    expect(wrapper.findAll(".skeleton").length).toBe(0);
    expect(wrapper.findAll(".stat-card").length).toBe(3);
  });

  it("clears error and shows loading on retry", async () => {
    let resolveRetry: (value: unknown) => void;
    apiMock
      .mockRejectedValueOnce(new Error("First failure"))
      .mockReturnValueOnce(new Promise((resolve) => { resolveRetry = resolve; }));

    const wrapper = mountPage();
    await flushPromises();

    // Error state
    expect(wrapper.find(".message").exists()).toBe(true);

    // Click retry
    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await nextTick();

    // During retry: error should be cleared, loading skeleton should show
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);

    // Resolve retry
    resolveRetry!({
      n_data: 1,
      n_complete_jobs: 0,
      n_models: 0,
      ins_datetime: "2025-01-01T00:00:00Z",
    });
    await flushPromises();

    // Success state
    expect(wrapper.find(".message").exists()).toBe(false);
    expect(wrapper.findAll(".stat-card").length).toBe(3);
  });

  it("shows error message after retry also fails", async () => {
    apiMock
      .mockRejectedValueOnce(new Error("First failure"))
      .mockRejectedValueOnce(new Error("Second failure"));

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("First failure");

    // Click retry
    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Second failure");
  });

  it("does not show empty state when loading", async () => {
    apiMock.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    await nextTick();

    expect(wrapper.find(".empty-state").exists()).toBe(false);
  });

  it("does not show empty state when error is displayed", async () => {
    apiMock.mockRejectedValueOnce(new Error("Fail"));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".empty-state").exists()).toBe(false);
    expect(wrapper.find(".message").exists()).toBe(true);
  });

  it("renders 3 skeleton placeholders during loading", async () => {
    apiMock.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    await nextTick();

    expect(wrapper.findAll(".skeleton").length).toBe(3);
  });

  it("covers Start Tuning button click handler with real Button", async () => {
    apiMock.mockResolvedValue({
      n_data: 3,
      n_complete_jobs: 1,
      n_models: 1,
      ins_datetime: "2025-01-01T00:00:00Z",
    });

    // Mount with real Button (no Button stub) to cover template @click handler
    const wrapper = mount(DashboardPage, {
      global: {
        plugins: [PrimeVue, createPinia(), i18n],
        provide: {
          currentProject: computed(() => null),
        },
        stubs: {
          Message: { template: '<div class="message"><slot /></div>' },
          Skeleton: { template: '<div class="skeleton" />' },
          StatCard: { template: '<div class="stat-card" />' },
          EmptyState: { template: '<div class="empty-state">{{ title }}<slot /></div>', props: ["icon", "title", "description"] },
        },
      },
    });
    await flushPromises();

    // Find the "Start Tuning" button rendered by real PrimeVue Button
    const startBtn = wrapper.findAll("button").find(b => b.text().includes("Start Tuning"));
    expect(startBtn).toBeDefined();
    await startBtn!.trigger("click");
    // The click handler calls router.push — mocked, so no error
  });

  describe("stepClass", () => {
    it("applies text-success class to completed pipeline steps", async () => {
      apiMock.mockResolvedValue({
        n_data: 5,
        n_complete_jobs: 3,
        n_models: 2,
      });

      const wrapper = mountPage();
      await flushPromises();

      // All three steps are done: Data (n_data > 0), Tuning (n_complete_jobs > 0), Model (n_models > 0)
      const stepSpans = wrapper.findAll("span.font-semibold.text-success");
      expect(stepSpans.length).toBe(3);
    });

    it("applies text-neutral-200 class to incomplete pipeline steps", async () => {
      apiMock.mockResolvedValue({
        n_data: 0,
        n_complete_jobs: 0,
        n_models: 0,
      });

      const wrapper = mountPage();
      await flushPromises();

      // All three steps are not done
      const incompleteSpans = wrapper.findAll("span.text-neutral-200");
      // There should be 3 step spans with text-neutral-200
      expect(incompleteSpans.length).toBeGreaterThanOrEqual(3);
      // No completed step spans
      const completedSpans = wrapper.findAll("span.font-semibold.text-success");
      expect(completedSpans.length).toBe(0);
    });

    it("applies mixed classes when some steps are complete", async () => {
      apiMock.mockResolvedValue({
        n_data: 3,
        n_complete_jobs: 0,
        n_models: 0,
      });

      const wrapper = mountPage();
      await flushPromises();

      // Only step 1 (Data) is done
      const completedSpans = wrapper.findAll("span.font-semibold.text-success");
      expect(completedSpans.length).toBe(1);
      expect(completedSpans[0].text()).toContain("1. Data");
    });
  });
});
