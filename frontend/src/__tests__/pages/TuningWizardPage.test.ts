import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import TuningWizardPage from "@/pages/TuningWizardPage.vue";
import PrimeVue from "primevue/config";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" }, query: {} }),
}));

const apiMock = vi.fn().mockResolvedValue({ results: [], count: 0 });
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({
    notify: { success: vi.fn(), error: vi.fn() },
  }),
}));

function mountPage() {
  return mount(TuningWizardPage, {
    global: {
      plugins: [PrimeVue, createPinia()],
      stubs: {
        Stepper: { template: '<div><slot /></div>' },
        StepList: { template: '<div><slot /></div>' },
        Step: { template: '<div><slot /></div>' },
        StepPanels: { template: '<div><slot /></div>' },
        StepPanel: { template: '<div><slot /></div>' },
        Select: true,
        InputNumber: true,
        Checkbox: true,
        Button: { template: '<button><slot /></button>', props: ['disabled'] },
      },
    },
  });
}

describe("TuningWizardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders page heading", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toContain("Tuning");
  });

  it("fetches training data on mount", () => {
    mountPage();
    expect(apiMock).toHaveBeenCalledWith(
      expect.stringContaining("training_data"),
      expect.anything()
    );
  });

  it("renders step labels", () => {
    const wrapper = mountPage();
    const text = wrapper.text();
    // The wizard has 4 steps: Data, Split, Evaluation, Run
    expect(text).toMatch(/Data|Split|Evaluation|Run/);
  });
});
