import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import ModelDetailPage from "@/pages/ModelDetailPage.vue";
import PrimeVue from "primevue/config";

const pushMock = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
  useRoute: () => ({ params: { projectId: "1", modelId: "10" } }),
}));

const apiMock = vi.fn().mockResolvedValue({
  id: 10,
  configuration: 1,
  data_loc: 1,
  irspack_version: "0.4.0",
  file: "/data/model.pkl",
  ins_datetime: "2025-01-01T00:00:00Z",
  basename: "model.pkl",
  filesize: 1024,
});

vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({
    notify: { success: vi.fn(), error: vi.fn() },
  }),
}));

function mountPage() {
  return mount(ModelDetailPage, {
    global: {
      plugins: [PrimeVue, createPinia()],
      stubs: {
        DataTable: { template: '<table><slot /></table>' },
        Column: true,
        InputText: true,
        InputNumber: true,
        Button: { template: '<button><slot /></button>' },
      },
    },
  });
}

describe("ModelDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders model heading", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toContain("Model");
  });

  it("fetches model data on mount", () => {
    mountPage();
    expect(apiMock).toHaveBeenCalledWith(
      expect.stringContaining("trained_model"),
    );
  });

  it("renders recommendation preview section", async () => {
    const wrapper = mountPage();
    await vi.dynamicImportSettled();
    // Should display recommendation input controls
    expect(wrapper.text()).toMatch(/Recommend|Preview|Fetch/i);
  });
});
