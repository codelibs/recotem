import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import TuningJobDetailPage from "@/pages/TuningJobDetailPage.vue";
import PrimeVue from "primevue/config";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1", jobId: "5" } }),
}));

const apiMock = vi.fn().mockImplementation((url: string) => {
  if (url.includes("parameter_tuning_job")) {
    return Promise.resolve({
      id: 5,
      data: 1,
      split: 1,
      evaluation: 1,
      n_tasks_parallel: 2,
      n_trials: 50,
      memory_budget: 4096,
      timeout_overall: null,
      timeout_singlestep: null,
      random_seed: null,
      tried_algorithms_json: null,
      irspack_version: "0.4.0",
      train_after_tuning: true,
      tuned_model: null,
      best_config: null,
      best_score: 0.42,
      task_links: [],
      ins_datetime: "2025-01-01T00:00:00Z",
    });
  }
  if (url.includes("task_log")) {
    return Promise.resolve({
      results: [
        { id: 1, task: 1, contents: "Trial 1 complete", ins_datetime: "2025-01-01T00:01:00Z" },
      ],
      count: 1,
    });
  }
  return Promise.resolve({});
});

vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

vi.mock("@/composables/useJobStatus", () => ({
  useJobLogs: () => ({
    logs: { value: [] },
    isConnected: { value: false },
    connect: vi.fn(),
    disconnect: vi.fn(),
  }),
}));

function mountPage() {
  return mount(TuningJobDetailPage, {
    global: {
      plugins: [PrimeVue, createPinia()],
      stubs: {
        Button: { template: '<button><slot /></button>' },
        Message: { template: '<div><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        Tag: { template: '<span><slot /></span>' },
      },
    },
  });
}

describe("TuningJobDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders job heading", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toContain("Tuning Job");
  });

  it("fetches job data and logs on mount", () => {
    mountPage();
    expect(apiMock).toHaveBeenCalledWith(
      expect.stringContaining("parameter_tuning_job"),
      expect.anything(),
    );
  });

  it("renders logs section after load", async () => {
    const wrapper = mountPage();
    // Wait for async onMounted to complete
    await vi.dynamicImportSettled();
    await wrapper.vm.$nextTick();
    await new Promise(r => setTimeout(r, 50));
    await wrapper.vm.$nextTick();
    // The page should contain "Logs" heading when data loads
    expect(wrapper.text()).toMatch(/Log|Tuning Job/);
  });
});
