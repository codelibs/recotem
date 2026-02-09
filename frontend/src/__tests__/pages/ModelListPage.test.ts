import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ModelListPage from "@/pages/ModelListPage.vue";
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
  return mount(ModelListPage, {
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

describe("ModelListPage", () => {
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
          basename: "model1",
          filesize: 5000,
          irspack_version: "0.4.0",
          ins_datetime: "2025-01-01T00:00:00Z",
        },
      ],
    });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
    expect(wrapper.find(".empty-state").exists()).toBe(false);
  });

  it("renders empty state when no models", async () => {
    apiMock.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No trained models yet.");
  });

  it("renders error message on failure", async () => {
    apiMock.mockRejectedValueOnce(new Error("Server error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Server error");
  });

  it("retries fetch on retry button click", async () => {
    apiMock
      .mockRejectedValueOnce(new Error("Server error"))
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

  describe("confirmDelete / executeDelete", () => {
    const mockModels = [
      { id: 1, basename: "model1", filesize: 5000, irspack_version: "0.4.0", ins_datetime: "2025-01-01T00:00:00Z" },
      { id: 2, basename: "model2", filesize: 8000, irspack_version: "0.4.0", ins_datetime: "2025-02-01T00:00:00Z" },
    ];

    it("shows delete confirmation dialog when confirmDelete is called", async () => {
      apiMock.mockResolvedValueOnce({ results: [...mockModels] });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      expect(vm.showDeleteConfirm).toBe(false);
      expect(vm.deleteTarget).toBeNull();

      vm.confirmDelete(mockModels[0]);
      await nextTick();

      expect(vm.showDeleteConfirm).toBe(true);
      expect(vm.deleteTarget).toEqual(mockModels[0]);
      expect(wrapper.find(".confirm-dialog").exists()).toBe(true);
    });

    it("removes model from list when delete is confirmed", async () => {
      apiMock
        .mockResolvedValueOnce({ results: [...mockModels] })
        .mockResolvedValueOnce(undefined);
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      vm.confirmDelete(mockModels[0]);
      await nextTick();

      const confirmDialog = wrapper.findComponent({ name: "ConfirmDialog" });
      confirmDialog.vm.$emit("confirm");
      await flushPromises();

      expect(apiMock).toHaveBeenCalledWith(
        expect.stringContaining("/trained_model/1/"),
        expect.objectContaining({ method: "DELETE" }),
      );
      expect(notifyMock.success).toHaveBeenCalled();
      expect(vm.models).toHaveLength(1);
      expect(vm.models[0].id).toBe(2);
    });

    it("shows error notification when delete fails", async () => {
      apiMock
        .mockResolvedValueOnce({ results: [...mockModels] })
        .mockRejectedValueOnce(new Error("Delete failed"));
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      vm.confirmDelete(mockModels[0]);
      await nextTick();

      const confirmDialog = wrapper.findComponent({ name: "ConfirmDialog" });
      confirmDialog.vm.$emit("confirm");
      await flushPromises();

      expect(notifyMock.error).toHaveBeenCalled();
      expect(vm.deleteTarget).toBeNull();
    });
  });
});
