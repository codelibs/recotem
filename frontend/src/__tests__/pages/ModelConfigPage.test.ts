import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ModelConfigPage from "@/pages/ModelConfigPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

const notifyMock = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => notifyMock,
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

function mountPage() {
  return mount(ModelConfigPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
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
          template: '<span class="tag">{{ value }}</span>',
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
        ConfirmDialog: {
          name: "ConfirmDialog",
          template:
            '<div class="confirm-dialog" v-if="visible"><button class="confirm-btn" @click="$emit(\'confirm\')">Confirm</button></div>',
          props: ["visible", "header", "message", "confirmLabel", "cancelLabel", "danger"],
          emits: ["confirm", "cancel", "update:visible"],
        },
        RouterLink: { template: "<a><slot /></a>" },
      },
    },
  });
}

describe("ModelConfigPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders loading state", async () => {
    apiMock.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    // DataTable is rendered with loading=true
    expect(wrapper.find(".data-table").exists()).toBe(true);
  });

  it("renders data table on success", async () => {
    apiMock.mockResolvedValue({
      results: [
        {
          id: 1,
          name: "cfg1",
          recommender_class_name: "IALSRecommender",
          parameters_json: { k: 10 },
          ins_datetime: "2025-01-01T00:00:00Z",
        },
      ],
    });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("renders error message on failure", async () => {
    apiMock.mockRejectedValueOnce(new Error("Fetch failed"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Fetch failed");
  });

  it("renders page title 'Model Configurations'", async () => {
    apiMock.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Model Configurations");
  });

  it("renders empty state when no configs", async () => {
    apiMock.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("renders config data with truncated JSON", async () => {
    apiMock.mockResolvedValue({
      results: [
        {
          id: 1,
          name: "cfg1",
          recommender_class_name: "IALSRecommender",
          parameters_json: { k: 10, alpha: 0.5 },
          ins_datetime: "2025-01-01T00:00:00Z",
        },
      ],
    });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
  });

  it("renders multiple configs from API response", async () => {
    apiMock.mockResolvedValue({
      results: [
        {
          id: 1,
          name: "cfg1",
          recommender_class_name: "IALSRecommender",
          parameters_json: { k: 10 },
          ins_datetime: "2025-01-01T00:00:00Z",
        },
        {
          id: 2,
          name: "cfg2",
          recommender_class_name: "P3alphaRecommender",
          parameters_json: { top_k: 100 },
          ins_datetime: "2025-02-01T00:00:00Z",
        },
      ],
    });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  describe("truncateJson / formatJson / openDetail", () => {
    const mockConfigs = [
      {
        id: 1,
        name: "cfg1",
        recommender_class_name: "IALSRecommender",
        parameters_json: { k: 10 },
        ins_datetime: "2025-01-01T00:00:00Z",
      },
    ];

    it("truncateJson returns full string if <= 60 chars", async () => {
      apiMock.mockResolvedValueOnce({ results: [...mockConfigs] });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      const result = vm.truncateJson({ k: 10 });
      expect(result).toBe('{"k":10}');
    });

    it("truncateJson truncates string if > 60 chars", async () => {
      apiMock.mockResolvedValueOnce({ results: [...mockConfigs] });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      const longObj = { alpha: 0.5, beta: 0.3, gamma: 0.8, delta: 0.1, epsilon: 0.9, zeta: 0.7 };
      const result = vm.truncateJson(longObj);
      expect(result.length).toBe(60);
      expect(result).toMatch(/\.\.\.$/);
    });

    it("formatJson returns pretty-printed JSON", async () => {
      apiMock.mockResolvedValueOnce({ results: [...mockConfigs] });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      const result = vm.formatJson({ k: 10, alpha: 0.5 });
      expect(result).toBe(JSON.stringify({ k: 10, alpha: 0.5 }, null, 2));
    });

    it("openDetail sets selectedConfig and shows detail dialog", async () => {
      apiMock.mockResolvedValueOnce({ results: [...mockConfigs] });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      expect(vm.showDetail).toBe(false);
      expect(vm.selectedConfig).toBeNull();

      vm.openDetail(mockConfigs[0]);
      await nextTick();

      expect(vm.showDetail).toBe(true);
      expect(vm.selectedConfig).toEqual(mockConfigs[0]);
      // Detail dialog should be visible
      expect(wrapper.find(".dialog").exists()).toBe(true);
    });
  });

  describe("confirmDelete / executeDelete", () => {
    const mockConfigs = [
      {
        id: 1,
        name: "cfg1",
        recommender_class_name: "IALSRecommender",
        parameters_json: { k: 10 },
        ins_datetime: "2025-01-01T00:00:00Z",
      },
      {
        id: 2,
        name: "cfg2",
        recommender_class_name: "P3alphaRecommender",
        parameters_json: { top_k: 100 },
        ins_datetime: "2025-02-01T00:00:00Z",
      },
    ];

    it("shows delete confirmation dialog when confirmDelete is called", async () => {
      apiMock.mockResolvedValueOnce({ results: [...mockConfigs] });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      expect(vm.showDeleteConfirm).toBe(false);
      expect(vm.deleteTarget).toBeNull();

      vm.confirmDelete(mockConfigs[0]);
      await nextTick();

      expect(vm.showDeleteConfirm).toBe(true);
      expect(vm.deleteTarget).toEqual(mockConfigs[0]);
      expect(wrapper.find(".confirm-dialog").exists()).toBe(true);
    });

    it("removes config from list when delete is confirmed", async () => {
      apiMock
        .mockResolvedValueOnce({ results: [...mockConfigs] })
        .mockResolvedValueOnce(undefined);
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      vm.confirmDelete(mockConfigs[0]);
      await nextTick();

      const confirmDialog = wrapper.findComponent({ name: "ConfirmDialog" });
      confirmDialog.vm.$emit("confirm");
      await flushPromises();

      expect(apiMock).toHaveBeenCalledWith(
        expect.stringContaining("/model_configuration/1/"),
        expect.objectContaining({ method: "DELETE" }),
      );
      expect(notifyMock.success).toHaveBeenCalled();
      expect(vm.configs).toHaveLength(1);
      expect(vm.configs[0].id).toBe(2);
    });

    it("shows error notification when delete fails", async () => {
      apiMock
        .mockResolvedValueOnce({ results: [...mockConfigs] })
        .mockRejectedValueOnce(new Error("Delete failed"));
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      vm.confirmDelete(mockConfigs[0]);
      await nextTick();

      const confirmDialog = wrapper.findComponent({ name: "ConfirmDialog" });
      confirmDialog.vm.$emit("confirm");
      await flushPromises();

      expect(notifyMock.error).toHaveBeenCalled();
      expect(vm.deleteTarget).toBeNull();
    });
  });
});
