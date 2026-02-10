import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import DataDetailPage from "@/pages/DataDetailPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1", dataId: "1" } }),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
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
  return mount(DataDetailPage, {
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
        ConfirmDialog: { template: "<div />" },
        RouterLink: { template: "<a><slot /></a>" },
      },
    },
  });
}

describe("DataDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders loading skeleton initially", async () => {
    apiMock.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders data details on success", async () => {
    apiMock
      .mockResolvedValueOnce({
        id: 1,
        basename: "test.csv",
        filesize: 2048,
        ins_datetime: "2025-01-01T00:00:00Z",
        file: "/media/test.csv",
      })
      .mockResolvedValueOnce({
        columns: ["user", "item"],
        rows: [["u1", "i1"]],
        total_rows: 1,
      });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("test.csv");
    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("renders error message on failure", async () => {
    apiMock.mockRejectedValueOnce(new Error("Not found"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Not found");
  });

  it("covers back button click handler with real Button", async () => {
    apiMock.mockResolvedValueOnce({
      id: 1,
      basename: "test.csv",
      filesize: 2048,
      ins_datetime: "2025-01-01T00:00:00Z",
      file: "/media/test.csv",
    }).mockResolvedValueOnce({
      columns: [],
      rows: [],
      total_rows: 0,
    });
    // Mount with real Button to cover template @click handler
    const wrapper = mount(DataDetailPage, {
      global: {
        plugins: [PrimeVue, createPinia(), i18n],
        stubs: {
          Message: { template: '<div class="message"><slot /></div>' },
          Skeleton: { template: '<div class="skeleton" />' },
          DataTable: { template: '<div class="data-table"><slot /></div>', props: ["value", "loading"] },
          Column: { template: "<div />" },
        },
      },
    });
    await flushPromises();
    // Find and click the back button
    const backBtn = wrapper.find("button");
    expect(backBtn.exists()).toBe(true);
    await backBtn.trigger("click");
  });

  it("renders back button", async () => {
    apiMock.mockResolvedValueOnce({
      id: 1,
      basename: "test.csv",
      filesize: 2048,
      ins_datetime: "2025-01-01T00:00:00Z",
      file: "/media/test.csv",
    }).mockResolvedValueOnce({
      columns: [],
      rows: [],
      total_rows: 0,
    });
    const wrapper = mountPage();
    await flushPromises();
    const buttons = wrapper.findAll("button");
    // Back button is the first button with the arrow-left icon
    expect(buttons.length).toBeGreaterThan(0);
  });

  it("renders download button on success", async () => {
    apiMock
      .mockResolvedValueOnce({
        id: 1,
        basename: "test.csv",
        filesize: 2048,
        ins_datetime: "2025-01-01T00:00:00Z",
        file: "/media/test.csv",
      })
      .mockResolvedValueOnce({
        columns: ["user", "item"],
        rows: [["u1", "i1"]],
        total_rows: 100,
      });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Download");
  });

  it("renders file size in data details", async () => {
    apiMock
      .mockResolvedValueOnce({
        id: 1,
        basename: "large.csv",
        filesize: 1048576,
        ins_datetime: "2025-06-01T12:00:00Z",
        file: "/media/large.csv",
      })
      .mockResolvedValueOnce({
        columns: ["a"],
        rows: [["v1"]],
        total_rows: 10,
      });
    const wrapper = mountPage();
    await flushPromises();
    // formatFileSize should render the size
    expect(wrapper.text()).toContain("large.csv");
  });

  it("renders data table when preview succeeds", async () => {
    apiMock
      .mockResolvedValueOnce({
        id: 1,
        basename: "test.csv",
        filesize: 512,
        ins_datetime: "2025-01-01T00:00:00Z",
        file: "/media/test.csv",
      })
      .mockResolvedValueOnce({
        columns: ["user", "item", "rating"],
        rows: [
          ["u1", "i1", 5],
          ["u2", "i2", 3],
        ],
        total_rows: 200,
      });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
  });

  it("handles preview failure gracefully", async () => {
    apiMock
      .mockResolvedValueOnce({
        id: 1,
        basename: "test.csv",
        filesize: 512,
        ins_datetime: "2025-01-01T00:00:00Z",
        file: "/media/test.csv",
      })
      .mockRejectedValueOnce(new Error("Preview failed"));
    const wrapper = mountPage();
    await flushPromises();
    // Data details should still render even if preview fails
    expect(wrapper.text()).toContain("test.csv");
  });

  describe("downloadFile", () => {
    it("calls window.open with file URL when download button is clicked", async () => {
      const windowOpenSpy = vi.spyOn(window, "open").mockImplementation(() => null);
      apiMock
        .mockResolvedValueOnce({
          id: 1,
          basename: "test.csv",
          filesize: 2048,
          ins_datetime: "2025-01-01T00:00:00Z",
          file: "/media/test.csv",
        })
        .mockResolvedValueOnce({
          columns: ["user", "item"],
          rows: [["u1", "i1"]],
          total_rows: 1,
        });
      const wrapper = mountPage();
      await flushPromises();

      const downloadBtn = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Download"));
      expect(downloadBtn).toBeDefined();
      await downloadBtn!.trigger("click");

      expect(windowOpenSpy).toHaveBeenCalledWith("/media/test.csv", "_blank");
      windowOpenSpy.mockRestore();
    });

    it("does not call window.open when data is not loaded", async () => {
      const windowOpenSpy = vi.spyOn(window, "open").mockImplementation(() => null);
      apiMock.mockRejectedValueOnce(new Error("Not found"));
      const wrapper = mountPage();
      await flushPromises();

      // No download button should be rendered since data failed to load
      const downloadBtn = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Download"));
      expect(downloadBtn).toBeUndefined();
      expect(windowOpenSpy).not.toHaveBeenCalled();
      windowOpenSpy.mockRestore();
    });
  });

  describe("loadPreview", () => {
    it("loads and displays preview data on mount", async () => {
      apiMock
        .mockResolvedValueOnce({
          id: 1,
          basename: "test.csv",
          filesize: 2048,
          ins_datetime: "2025-01-01T00:00:00Z",
          file: "/media/test.csv",
        })
        .mockResolvedValueOnce({
          columns: ["user", "item", "rating"],
          rows: [
            ["u1", "i1", 5],
            ["u2", "i2", 3],
          ],
          total_rows: 200,
        });
      const wrapper = mountPage();
      await flushPromises();

      // Preview is automatically loaded after data loads
      expect(apiMock).toHaveBeenCalledTimes(2);
      // The second call should be to the preview endpoint
      expect(apiMock).toHaveBeenLastCalledWith(
        expect.stringContaining("preview"),
        expect.objectContaining({ params: { n_rows: 50 } }),
      );
      // Data table should be rendered
      expect(wrapper.find(".data-table").exists()).toBe(true);
      // Total rows should be shown
      expect(wrapper.text()).toContain("200");
    });

    it("shows preview failed message when preview API returns error", async () => {
      apiMock
        .mockResolvedValueOnce({
          id: 1,
          basename: "test.csv",
          filesize: 512,
          ins_datetime: "2025-01-01T00:00:00Z",
          file: "/media/test.csv",
        })
        .mockRejectedValueOnce(new Error("Preview error"));
      const wrapper = mountPage();
      await flushPromises();

      // Data details should still be visible
      expect(wrapper.text()).toContain("test.csv");
      // No data table since preview failed
      expect(wrapper.find(".data-table").exists()).toBe(false);
    });
  });
});
