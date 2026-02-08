import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import TuningWizardPage from "@/pages/TuningWizardPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ params: { projectId: "1" }, query: {} }),
}));

const apiMock = vi.fn().mockResolvedValue({ results: [], count: 0 });
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  unwrapResults: (res: any) => Array.isArray(res) ? res : res.results,
}));

const notifyMock = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => notifyMock,
}));

function mountPage() {
  return mount(TuningWizardPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Stepper: { template: '<div class="stepper"><slot /></div>' },
        StepList: { template: '<div class="step-list"><slot /></div>' },
        Step: { template: '<div class="step"><slot /></div>' },
        StepPanels: { template: '<div class="step-panels"><slot /></div>' },
        StepPanel: { template: '<div class="step-panel"><slot /></div>' },
        Select: {
          template: '<select class="select-stub" @change="$emit(\'update:modelValue\', Number($event.target.value))"><option v-for="o in options" :key="o[optionValue]" :value="o[optionValue]">{{ o[optionLabel] }}</option></select>',
          props: ["modelValue", "options", "optionLabel", "optionValue", "placeholder"],
          emits: ["update:modelValue"],
        },
        InputNumber: true,
        Checkbox: true,
        FormField: { template: '<div class="form-field"><label>{{ label }}</label><slot /></div>', props: ["label", "name", "tooltip"] },
        Button: {
          template: '<button :disabled="$attrs.disabled" @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
      },
    },
  });
}

const mockTrainingData = [
  { id: 10, project: 1, file: "/data/movies.csv", ins_datetime: "2025-01-01T00:00:00Z", basename: "movies.csv", filesize: 1024 },
  { id: 20, project: 1, file: "/data/ratings.csv", ins_datetime: "2025-01-02T00:00:00Z", basename: "ratings.csv", filesize: 2048 },
];

describe("TuningWizardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders page heading", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Tuning");
  });

  it("fetches training data on mount", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    mountPage();
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      expect.stringContaining("training_data"),
      expect.anything()
    );
  });

  it("renders step labels", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();
    const text = wrapper.text();
    // The wizard has 4 steps: Data, Split, Evaluation, Run
    expect(text).toMatch(/Data|Split|Evaluation|Run/);
  });

  // --- Error scenario tests ---

  it("disables Next button when no training data is selected", async () => {
    apiMock.mockResolvedValue({ results: mockTrainingData, count: 2 });
    const wrapper = mountPage();
    await flushPromises();

    // Find the Next button in the first step panel
    const buttons = wrapper.findAll("button");
    const nextBtn = buttons.find(b => b.text().includes("Next"));
    expect(nextBtn).toBeDefined();
    expect(nextBtn!.attributes("disabled")).toBeDefined();
  });

  it("enables Next button when training data is selected", async () => {
    apiMock.mockResolvedValue({ results: mockTrainingData, count: 2 });
    const wrapper = mountPage();
    await flushPromises();

    // Select a training data option
    const select = wrapper.find(".select-stub");
    await select.setValue(10);
    await nextTick();

    const buttons = wrapper.findAll("button");
    const nextBtn = buttons.find(b => b.text().includes("Next"));
    expect(nextBtn).toBeDefined();
    // After selecting data, the disabled attribute should not be set
    expect(nextBtn!.attributes("disabled")).toBeUndefined();
  });

  it("calls notify.error when job submission fails", async () => {
    // First call: fetch training data
    apiMock
      .mockResolvedValueOnce({ results: mockTrainingData, count: 2 })
      // split config POST
      .mockResolvedValueOnce({ id: 100 })
      // evaluation config POST
      .mockResolvedValueOnce({ id: 200 })
      // tuning job POST - fails
      .mockRejectedValueOnce(new Error("Server error"));

    const wrapper = mountPage();
    await flushPromises();

    // Set form data (simulate that data is selected)
    const select = wrapper.find(".select-stub");
    await select.setValue(10);
    await nextTick();

    // Find the "Start Tuning" button
    const startBtn = wrapper.findAll("button").find(b => b.text().includes("Start Tuning"));
    expect(startBtn).toBeDefined();
    await startBtn!.trigger("click");
    await flushPromises();

    expect(notifyMock.error).toHaveBeenCalledWith("Failed to create tuning job");
  });

  it("calls notify.success and navigates on successful submission", async () => {
    apiMock
      .mockResolvedValueOnce({ results: mockTrainingData, count: 2 })
      .mockResolvedValueOnce({ id: 100 })
      .mockResolvedValueOnce({ id: 200 })
      .mockResolvedValueOnce({ id: 42, data: 10 });

    const wrapper = mountPage();
    await flushPromises();

    const select = wrapper.find(".select-stub");
    await select.setValue(10);
    await nextTick();

    const startBtn = wrapper.findAll("button").find(b => b.text().includes("Start Tuning"));
    await startBtn!.trigger("click");
    await flushPromises();

    expect(notifyMock.success).toHaveBeenCalledWith("Tuning job created");
    expect(mockPush).toHaveBeenCalledWith("/projects/1/tuning/42");
  });

  it("does not submit when form.data is null", async () => {
    apiMock.mockResolvedValue({ results: mockTrainingData, count: 2 });
    const wrapper = mountPage();
    await flushPromises();

    // Do NOT select any data

    // Find the "Start Tuning" button and click it
    const startBtn = wrapper.findAll("button").find(b => b.text().includes("Start Tuning"));
    expect(startBtn).toBeDefined();
    await startBtn!.trigger("click");
    await flushPromises();

    // submitJob returns early if !form.data -- no POST calls beyond the initial GET
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it("shows the split configuration step fields", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Split Configuration");
    expect(text).toContain("Heldout Ratio");
    expect(text).toContain("Test User Ratio");
  });

  it("shows the evaluation configuration step fields", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Evaluation Configuration");
    expect(text).toContain("Target Metric");
    expect(text).toContain("Cutoff");
  });

  it("shows the job configuration step fields", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Job Configuration");
    expect(text).toContain("Number of Trials");
    expect(text).toContain("Parallel Tasks");
    expect(text).toContain("Memory Budget");
  });

  it("shows error notification when split config creation fails", async () => {
    apiMock
      .mockResolvedValueOnce({ results: mockTrainingData, count: 2 })
      // split config POST fails
      .mockRejectedValueOnce(new Error("Validation error"));

    const wrapper = mountPage();
    await flushPromises();

    const select = wrapper.find(".select-stub");
    await select.setValue(10);
    await nextTick();

    const startBtn = wrapper.findAll("button").find(b => b.text().includes("Start Tuning"));
    await startBtn!.trigger("click");
    await flushPromises();

    expect(notifyMock.error).toHaveBeenCalledWith("Failed to create tuning job");
  });
});
