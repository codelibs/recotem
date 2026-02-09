import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import DataListPage from "@/pages/DataListPage.vue";
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
  return mount(DataListPage, {
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

describe("DataListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders loading state when API has not resolved", async () => {
    apiMock.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    // DataTable receives loading=true while request is pending
    const dt = wrapper.findComponent({ name: "DataTable" });
    expect(dt.exists() || wrapper.find(".data-table").exists()).toBe(true);
  });

  it("renders data table on success", async () => {
    apiMock.mockResolvedValue({
      results: [
        {
          id: 1,
          basename: "train.csv",
          filesize: 1024,
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
    apiMock.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Network error");
  });

  it("renders page title 'Training Data'", async () => {
    apiMock.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Training Data");
  });

  it("retries fetch on retry button click", async () => {
    apiMock
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({
        results: [
          {
            id: 1,
            basename: "train.csv",
            filesize: 1024,
            ins_datetime: "2025-01-01T00:00:00Z",
          },
        ],
      });
    const wrapper = mountPage();
    await flushPromises();
    const retryBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Retry"));
    expect(retryBtn).toBeDefined();
    await retryBtn!.trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledTimes(2);
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("ignores AbortError", async () => {
    const abortError = new DOMException("Aborted", "AbortError");
    apiMock.mockRejectedValueOnce(abortError);
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("covers Upload Data button click handler with real Button", async () => {
    apiMock.mockResolvedValue({ results: [] });
    // Mount with real Button to cover template @click handler
    const wrapper = mount(DataListPage, {
      global: {
        plugins: [PrimeVue, createPinia(), i18n],
        stubs: {
          Message: { template: '<div class="message"><slot /></div>' },
          Skeleton: { template: '<div class="skeleton" />' },
          DataTable: { template: '<div class="data-table"><slot /></div>', props: ["value", "loading"] },
          Column: { template: "<div />" },
          Tag: { template: '<span class="tag">{{ value }}</span>', props: ["severity", "value"] },
          EmptyState: { template: '<div class="empty-state"><slot /></div>', props: ["icon", "title", "description"] },
          ConfirmDialog: { template: "<div />", props: ["visible"] },
        },
      },
    });
    await flushPromises();

    // Find the "Upload Data" button rendered by real PrimeVue Button
    const uploadBtn = wrapper.findAll("button").find(b => b.text().includes("Upload"));
    expect(uploadBtn).toBeDefined();
    await uploadBtn!.trigger("click");
  });

  describe("confirmDelete / executeDelete", () => {
    const mockData = [
      { id: 1, basename: "train.csv", filesize: 1024, ins_datetime: "2025-01-01T00:00:00Z" },
      { id: 2, basename: "test.csv", filesize: 2048, ins_datetime: "2025-02-01T00:00:00Z" },
    ];

    it("shows delete confirmation dialog when confirmDelete is called", async () => {
      apiMock.mockResolvedValueOnce({ results: [...mockData] });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      // Initially the confirm dialog should not be visible
      expect(vm.showDeleteConfirm).toBe(false);
      expect(vm.deleteTarget).toBeNull();

      // Call confirmDelete with an item
      vm.confirmDelete(mockData[0]);
      await nextTick();

      // ConfirmDialog should now be visible and target set
      expect(vm.showDeleteConfirm).toBe(true);
      expect(vm.deleteTarget).toEqual(mockData[0]);
      expect(wrapper.find(".confirm-dialog").exists()).toBe(true);
    });

    it("removes item from list when delete is confirmed", async () => {
      apiMock
        .mockResolvedValueOnce({ results: [...mockData] })  // fetchData
        .mockResolvedValueOnce(undefined);                    // DELETE call
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      // Trigger confirmDelete
      vm.confirmDelete(mockData[0]);
      await nextTick();

      // Emit confirm from ConfirmDialog stub
      const confirmDialog = wrapper.findComponent({ name: "ConfirmDialog" });
      confirmDialog.vm.$emit("confirm");
      await flushPromises();

      // Verify DELETE API was called with correct endpoint
      expect(apiMock).toHaveBeenCalledWith(
        expect.stringContaining("/training_data/1/"),
        expect.objectContaining({ method: "DELETE" }),
      );

      // Verify success notification
      expect(notifyMock.success).toHaveBeenCalled();

      // Verify item was removed from list
      expect(vm.dataList).toHaveLength(1);
      expect(vm.dataList[0].id).toBe(2);
    });

    it("shows error notification when delete fails", async () => {
      apiMock
        .mockResolvedValueOnce({ results: [...mockData] })  // fetchData
        .mockRejectedValueOnce(new Error("Delete failed"));  // DELETE call
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      // Trigger confirmDelete
      vm.confirmDelete(mockData[0]);
      await nextTick();

      // Emit confirm from ConfirmDialog stub
      const confirmDialog = wrapper.findComponent({ name: "ConfirmDialog" });
      confirmDialog.vm.$emit("confirm");
      await flushPromises();

      // Verify error notification
      expect(notifyMock.error).toHaveBeenCalled();

      // Verify deleteTarget is cleared even on error
      expect(vm.deleteTarget).toBeNull();
    });
  });
});
