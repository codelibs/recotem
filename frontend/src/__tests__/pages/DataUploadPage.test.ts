import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick, defineComponent } from "vue";
import { createPinia, setActivePinia } from "pinia";
import DataUploadPage from "@/pages/DataUploadPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

const notifyMock = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => notifyMock,
}));

vi.mock("@/api/config", () => ({
  toApiUrl: (path: string) => `/api/v1/${path.replace(/^\/+/, "")}`,
}));

// XHR mock infrastructure
let xhrInstances: MockXHR[] = [];
class MockXHR {
  status = 200;
  responseText = "";
  private listeners: Record<string, ((...args: any[]) => void)[]> = {};
  upload = {
    listeners: {} as Record<string, ((...args: any[]) => void)[]>,
    addEventListener(event: string, handler: (...args: any[]) => void) {
      (this.listeners[event] ??= []).push(handler);
    },
  };

  addEventListener(event: string, handler: (...args: any[]) => void) {
    (this.listeners[event] ??= []).push(handler);
  }

  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn();

  // test helpers
  triggerUploadProgress(loaded: number, total: number) {
    for (const h of this.upload.listeners["progress"] ?? []) {
      h({ lengthComputable: true, loaded, total });
    }
  }

  triggerLoad(status: number, responseText = "") {
    this.status = status;
    this.responseText = responseText;
    for (const h of this.listeners["load"] ?? []) h();
  }

  triggerError() {
    for (const h of this.listeners["error"] ?? []) h();
  }

  triggerAbort() {
    for (const h of this.listeners["abort"] ?? []) h();
  }
}

// Named stub component so findComponent works
const FileUploadStub = defineComponent({
  name: "FileUpload",
  props: ["mode", "accept", "maxFileSize", "auto", "chooseLabel", "customUpload", "disabled"],
  emits: ["upload", "error", "uploader"],
  template: `<div class="file-upload">
    <input type="file" :accept="accept" :disabled="disabled" />
    <slot name="empty" />
  </div>`,
});

function mountPage() {
  return mount(DataUploadPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        FileUpload: FileUploadStub,
        ProgressBar: { template: '<div class="progress-bar" :data-value="value" />', props: ["value"] },
        Button: {
          template: '<button :data-label="$attrs.label" :disabled="$attrs.disabled" @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Message: { template: '<div class="message" :data-severity="severity"><slot /></div>', props: ["severity", "closable"] },
      },
    },
  });
}

/** Helper: emits the uploader event with a file, triggering handleUpload */
function emitUploader(wrapper: ReturnType<typeof mountPage>, file: File) {
  const fu = wrapper.findComponent(FileUploadStub);
  fu.vm.$emit("uploader", { files: [file] });
}

function emitUploaderEmpty(wrapper: ReturnType<typeof mountPage>) {
  const fu = wrapper.findComponent(FileUploadStub);
  fu.vm.$emit("uploader", { files: [] });
}

describe("DataUploadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    xhrInstances = [];
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => {
        const instance = new MockXHR();
        xhrInstances.push(instance);
        return instance;
      }),
    );
  });

  it("renders upload heading", () => {
    const wrapper = mountPage();
    expect(wrapper.find("h2").exists()).toBe(true);
    expect(wrapper.text()).toContain("Upload");
  });

  it("has file input that accepts CSV files", () => {
    const wrapper = mountPage();
    const input = wrapper.find('input[type="file"]');
    expect(input.exists()).toBe(true);
    expect(input.attributes("accept")).toBe(".csv,.csv.gz");
  });

  it("renders drag-and-drop empty state", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toMatch(/drag|drop|CSV/i);
  });

  it("does not show progress bar initially", () => {
    const wrapper = mountPage();
    expect(wrapper.find(".progress-bar").exists()).toBe(false);
  });

  it("does not show error message initially", () => {
    const wrapper = mountPage();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("renders back button that navigates to data list", async () => {
    const wrapper = mountPage();
    const buttons = wrapper.findAll("button");
    expect(buttons.length).toBeGreaterThan(0);
    await buttons[0].trigger("click");
    expect(mockPush).toHaveBeenCalledWith("/projects/1/data");
  });

  describe("handleUpload", () => {
    it("starts upload when file is provided via uploader event", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      // Should show progress bar while uploading
      expect(wrapper.find(".progress-bar").exists()).toBe(true);
      // XHR should have been created and sent
      expect(xhrInstances.length).toBe(1);
      expect(xhrInstances[0].open).toHaveBeenCalledWith("POST", expect.stringContaining("training_data"));
      expect(xhrInstances[0].send).toHaveBeenCalled();
    });

    it("does nothing when event has no files", async () => {
      const wrapper = mountPage();

      emitUploaderEmpty(wrapper);
      await nextTick();

      expect(wrapper.find(".progress-bar").exists()).toBe(false);
      expect(xhrInstances.length).toBe(0);
    });
  });

  describe("upload progress", () => {
    it("updates progress bar during upload", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerUploadProgress(50, 100);
      await nextTick();

      const progressBar = wrapper.find(".progress-bar");
      expect(progressBar.exists()).toBe(true);
      expect(progressBar.attributes("data-value")).toBe("50");
    });
  });

  describe("upload success", () => {
    it("shows success notification and redirects on successful upload", async () => {
      vi.useFakeTimers();
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerLoad(200);
      await flushPromises();

      expect(notifyMock.success).toHaveBeenCalled();
      // After 500ms, should redirect
      vi.advanceTimersByTime(500);
      expect(mockPush).toHaveBeenCalledWith("/projects/1/data");

      vi.useRealTimers();
    });

    it("hides progress bar after successful upload", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();
      expect(wrapper.find(".progress-bar").exists()).toBe(true);

      xhrInstances[0].triggerLoad(200);
      await flushPromises();

      expect(wrapper.find(".progress-bar").exists()).toBe(false);
    });
  });

  describe("upload failure", () => {
    it("shows error message on server error with detail", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerLoad(400, JSON.stringify({ detail: "Invalid CSV format" }));
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
      expect(wrapper.text()).toContain("Invalid CSV format");
    });

    it("shows error message on server error with array response", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerLoad(400, JSON.stringify(["File too large"]));
      await flushPromises();

      expect(wrapper.text()).toContain("File too large");
    });

    it("shows error message on server error with file field error", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerLoad(400, JSON.stringify({ file: ["This field is required."] }));
      await flushPromises();

      expect(wrapper.text()).toContain("This field is required.");
    });

    it("shows generic error when response is not parseable JSON", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerLoad(500, "Internal Server Error");
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("shows error on network failure", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerError();
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("shows error on upload abort", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      xhrInstances[0].triggerAbort();
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("hides progress bar after failed upload", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();
      expect(wrapper.find(".progress-bar").exists()).toBe(true);

      xhrInstances[0].triggerLoad(400, JSON.stringify({ detail: "Bad request" }));
      await flushPromises();

      expect(wrapper.find(".progress-bar").exists()).toBe(false);
    });
  });

  describe("retryUpload", () => {
    it("shows retry button in error state and retries on click", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      // Trigger first upload that fails
      emitUploader(wrapper, mockFile);
      await nextTick();
      xhrInstances[0].triggerLoad(500, "error");
      await flushPromises();

      // Retry button should appear
      const retryBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("retry"));
      expect(retryBtn).toBeDefined();

      // Click retry
      await retryBtn!.trigger("click");
      await nextTick();

      // A new XHR should be created
      expect(xhrInstances.length).toBe(2);
      expect(xhrInstances[1].send).toHaveBeenCalled();
    });
  });

  describe("onUpload and onError callbacks", () => {
    it("calls notify.success when onUpload is triggered", async () => {
      const wrapper = mountPage();
      const fu = wrapper.findComponent(FileUploadStub);
      fu.vm.$emit("upload");
      await nextTick();
      expect(notifyMock.success).toHaveBeenCalled();
    });

    it("calls notify.error when onError is triggered", async () => {
      const wrapper = mountPage();
      const fu = wrapper.findComponent(FileUploadStub);
      fu.vm.$emit("error");
      await nextTick();
      expect(notifyMock.error).toHaveBeenCalled();
    });
  });

  describe("authorization header", () => {
    it("sets Authorization header with bearer token on XHR", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await nextTick();

      expect(xhrInstances[0].setRequestHeader).toHaveBeenCalledWith(
        "Authorization",
        expect.stringMatching(/^Bearer /),
      );
    });
  });
});
