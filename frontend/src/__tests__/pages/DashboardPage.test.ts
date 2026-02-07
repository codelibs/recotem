import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import DashboardPage from "@/pages/DashboardPage.vue";
import PrimeVue from "primevue/config";

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
}));

function mountPage() {
  return mount(DashboardPage, {
    global: {
      plugins: [PrimeVue, createPinia()],
      stubs: {
        Button: { template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        StatCard: { template: '<div class="stat-card" />' },
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
    expect(wrapper.text()).toContain("Failed to load");
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
});
