import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ModelComparisonPage from "@/pages/ModelComparisonPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

const apiMock = vi.fn();
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  classifyApiError: (err: any) => ({ kind: "unknown", status: undefined, message: err?.message ?? "Unknown error", fieldErrors: undefined }),
  unwrapResults: (res: any) => Array.isArray(res) ? res : res.results,
}));

function mountPage() {
  return mount(ModelComparisonPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: {
          template:
            '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Checkbox: {
          template: '<input type="checkbox" />',
          inheritAttrs: false,
        },
        Message: { template: '<div class="message"><slot /></div>' },
      },
    },
  });
}

const mockJobs = [
  {
    id: 1,
    data: 1,
    split: 1,
    evaluation: 1,
    status: "COMPLETED",
    n_tasks_parallel: 2,
    n_trials: 40,
    memory_budget: 8000,
    timeout_overall: null,
    timeout_singlestep: null,
    random_seed: 42,
    tried_algorithms_json: null,
    irspack_version: "0.4.0",
    train_after_tuning: true,
    tuned_model: 1,
    best_config: 1,
    best_score: 0.1234,
    task_links: [],
    ins_datetime: "2025-01-01T00:00:00Z",
  },
  {
    id: 2,
    data: 1,
    split: 1,
    evaluation: 1,
    status: "COMPLETED",
    n_tasks_parallel: 4,
    n_trials: 80,
    memory_budget: 16000,
    timeout_overall: null,
    timeout_singlestep: null,
    random_seed: 123,
    tried_algorithms_json: null,
    irspack_version: "0.4.0",
    train_after_tuning: true,
    tuned_model: 2,
    best_config: 2,
    best_score: 0.5678,
    task_links: [],
    ins_datetime: "2025-01-02T00:00:00Z",
  },
];

describe("ModelComparisonPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders heading", async () => {
    apiMock.mockResolvedValueOnce({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Model Comparison");
  });

  it("shows empty state when no completed jobs", async () => {
    apiMock.mockResolvedValueOnce({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("No completed tuning jobs available");
  });

  it("loads completed jobs on mount", async () => {
    apiMock.mockResolvedValueOnce({ results: mockJobs });
    const wrapper = mountPage();
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      "/parameter_tuning_job/",
      expect.objectContaining({ params: { data__project: "1" } })
    );
    expect(wrapper.text()).toContain("Job #1");
    expect(wrapper.text()).toContain("Job #2");
  });

  it("shows error message when API fails", async () => {
    apiMock.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
  });

  it("filters out jobs without best_config", async () => {
    const jobsWithIncomplete = [
      ...mockJobs,
      {
        ...mockJobs[0],
        id: 3,
        best_config: null,
        best_score: null,
      },
    ];
    apiMock.mockResolvedValueOnce({ results: jobsWithIncomplete });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Job #1");
    expect(wrapper.text()).toContain("Job #2");
    expect(wrapper.text()).not.toContain("Job #3");
  });
});
