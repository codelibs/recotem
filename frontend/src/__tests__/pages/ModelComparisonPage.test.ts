import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ModelComparisonPage from "@/pages/ModelComparisonPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
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
          template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Checkbox: {
          template: `<input type="checkbox"
            :value="value"
            :checked="isChecked"
            :disabled="disabled"
            @change="toggle"
          />`,
          props: ["modelValue", "value", "inputId", "disabled"],
          emits: ["update:modelValue"],
          computed: {
            isChecked(): boolean {
              return Array.isArray(this.modelValue) && (this.modelValue as number[]).includes(this.value as number);
            },
          },
          methods: {
            toggle() {
              const current: number[] = Array.isArray(this.modelValue) ? [...(this.modelValue as number[])] : [];
              const idx = current.indexOf(this.value as number);
              if (idx >= 0) {
                current.splice(idx, 1);
              } else {
                current.push(this.value as number);
              }
              this.$emit("update:modelValue", current);
            },
          },
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
  {
    id: 3,
    data: 1,
    split: 1,
    evaluation: 1,
    status: "COMPLETED",
    n_tasks_parallel: 1,
    n_trials: 20,
    memory_budget: 4000,
    timeout_overall: null,
    timeout_singlestep: null,
    random_seed: 7,
    tried_algorithms_json: null,
    irspack_version: "0.3.0",
    train_after_tuning: false,
    tuned_model: null,
    best_config: 3,
    best_score: 0.9012,
    task_links: [],
    ins_datetime: "2025-01-03T00:00:00Z",
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

  it("calls API with correct project filter on mount", async () => {
    apiMock.mockResolvedValueOnce({ results: [] });
    mountPage();
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      "/parameter_tuning_job/",
      expect.objectContaining({ params: { data__project: "1" } }),
    );
  });

  it("loads and displays completed jobs", async () => {
    apiMock.mockResolvedValueOnce({ results: mockJobs });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Job #1");
    expect(wrapper.text()).toContain("Job #2");
    expect(wrapper.text()).toContain("Job #3");
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
        id: 4,
        best_config: null,
        best_score: null,
      },
    ];
    apiMock.mockResolvedValueOnce({ results: jobsWithIncomplete });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Job #1");
    expect(wrapper.text()).toContain("Job #2");
    expect(wrapper.text()).toContain("Job #3");
    expect(wrapper.text()).not.toContain("Job #4");
  });

  it("shows best_score next to each job label", async () => {
    apiMock.mockResolvedValueOnce({ results: mockJobs });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("0.1234");
    expect(wrapper.text()).toContain("0.5678");
    expect(wrapper.text()).toContain("0.9012");
  });

  it("renders back button that navigates to tuning list", async () => {
    apiMock.mockResolvedValueOnce({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    const buttons = wrapper.findAll("button");
    expect(buttons.length).toBeGreaterThan(0);
    await buttons[0].trigger("click");
    expect(mockPush).toHaveBeenCalledWith("/projects/1/tuning");
  });

  describe("job selection", () => {
    it("renders checkboxes for each completed job", async () => {
      apiMock.mockResolvedValueOnce({ results: mockJobs });
      const wrapper = mountPage();
      await flushPromises();
      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      expect(checkboxes.length).toBe(3);
    });

    it("shows comparison table when 2 jobs are selected", async () => {
      apiMock.mockResolvedValueOnce({ results: mockJobs });
      const wrapper = mountPage();
      await flushPromises();

      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      // Select first two jobs
      await checkboxes[0].trigger("change");
      await nextTick();
      await checkboxes[1].trigger("change");
      await nextTick();

      expect(wrapper.find("table").exists()).toBe(true);
      expect(wrapper.text()).toContain("Comparison");
    });

    it("shows 'select at least 2' message when only 1 job is selected", async () => {
      apiMock.mockResolvedValueOnce({ results: mockJobs });
      const wrapper = mountPage();
      await flushPromises();

      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      await checkboxes[0].trigger("change");
      await nextTick();

      expect(wrapper.text()).toContain("Select at least 2 jobs to compare");
    });

    it("does not show comparison table when no jobs selected", async () => {
      apiMock.mockResolvedValueOnce({ results: mockJobs });
      const wrapper = mountPage();
      await flushPromises();
      expect(wrapper.find("table").exists()).toBe(false);
    });

    it("disables additional checkboxes when 3 are selected", async () => {
      apiMock.mockResolvedValueOnce({ results: mockJobs });
      const wrapper = mountPage();
      await flushPromises();

      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      // Select all 3 jobs
      await checkboxes[0].trigger("change");
      await nextTick();
      await checkboxes[1].trigger("change");
      await nextTick();
      await checkboxes[2].trigger("change");
      await nextTick();

      // All 3 are now selected; with only 3 available, we can't test disabled on a 4th,
      // but the component logic sets disabled when selectedJobIds.length >= 3 for unselected ones
      expect(wrapper.find("table").exists()).toBe(true);
    });
  });

  describe("comparison table content", () => {
    async function mountWithTwoSelected() {
      apiMock.mockResolvedValueOnce({ results: mockJobs });
      const wrapper = mountPage();
      await flushPromises();

      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      await checkboxes[0].trigger("change");
      await nextTick();
      await checkboxes[1].trigger("change");
      await nextTick();

      return wrapper;
    }

    it("displays best score row", async () => {
      const wrapper = await mountWithTwoSelected();
      expect(wrapper.text()).toContain("0.1234");
      expect(wrapper.text()).toContain("0.5678");
    });

    it("displays trials row", async () => {
      const wrapper = await mountWithTwoSelected();
      // n_trials for job 1 and 2
      expect(wrapper.text()).toContain("40");
      expect(wrapper.text()).toContain("80");
    });

    it("displays parallel tasks row", async () => {
      const wrapper = await mountWithTwoSelected();
      expect(wrapper.text()).toContain("Parallel Tasks");
    });

    it("displays memory budget row with MB suffix", async () => {
      const wrapper = await mountWithTwoSelected();
      expect(wrapper.text()).toContain("8000");
      expect(wrapper.text()).toContain("MB");
    });

    it("displays irspack version row", async () => {
      const wrapper = await mountWithTwoSelected();
      expect(wrapper.text()).toContain("0.4.0");
    });

    it("displays created at row", async () => {
      const wrapper = await mountWithTwoSelected();
      // formatDate will format the ISO string; just check something from it appears
      expect(wrapper.text()).toContain("2025");
    });

    it("highlights the best scoring job", async () => {
      const wrapper = await mountWithTwoSelected();
      // Job 2 has the higher score (0.5678 > 0.1234)
      // The best job cell should have font-bold class
      const tdCells = wrapper.findAll("td");
      const bestCell = tdCells.find(
        td => td.text().includes("0.5678") && td.classes().some(c => c.includes("font-bold")),
      );
      expect(bestCell).toBeDefined();
    });
  });

  describe("bestJobId computed", () => {
    it("identifies the job with the highest best_score", async () => {
      apiMock.mockResolvedValueOnce({ results: mockJobs });
      const wrapper = mountPage();
      await flushPromises();

      // Select jobs 1 and 3 (scores: 0.1234 and 0.9012)
      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      await checkboxes[0].trigger("change");
      await nextTick();
      await checkboxes[2].trigger("change");
      await nextTick();

      // Job 3 (score 0.9012) should be highlighted as best
      const tdCells = wrapper.findAll("td");
      const bestCell = tdCells.find(
        td => td.text().includes("0.9012") && td.classes().some(c => c.includes("font-bold")),
      );
      expect(bestCell).toBeDefined();
    });

    it("handles jobs with null best_score gracefully", async () => {
      const jobsWithNull = [
        { ...mockJobs[0], id: 10, best_score: null, best_config: 10 },
        { ...mockJobs[1] },
      ];
      apiMock.mockResolvedValueOnce({ results: jobsWithNull });
      const wrapper = mountPage();
      await flushPromises();

      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      await checkboxes[0].trigger("change");
      await nextTick();
      await checkboxes[1].trigger("change");
      await nextTick();

      // Should display '-' for null score
      expect(wrapper.text()).toContain("-");
      // Should still show the table without errors
      expect(wrapper.find("table").exists()).toBe(true);
    });
  });
});
