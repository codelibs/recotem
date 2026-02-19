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
      await flushPromises();

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
      await flushPromises();

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
      await flushPromises();

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
      await flushPromises();
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
      await flushPromises();

      xhrInstances[0].triggerLoad(400, JSON.stringify({ detail: "Invalid CSV format" }));
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
      expect(wrapper.text()).toContain("Invalid CSV format");
    });

    it("shows error message on server error with array response", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await flushPromises();

      xhrInstances[0].triggerLoad(400, JSON.stringify(["File too large"]));
      await flushPromises();

      expect(wrapper.text()).toContain("File too large");
    });

    it("shows error message on server error with file field error", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await flushPromises();

      xhrInstances[0].triggerLoad(400, JSON.stringify({ file: ["This field is required."] }));
      await flushPromises();

      expect(wrapper.text()).toContain("This field is required.");
    });

    it("shows generic error when response is not parseable JSON", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await flushPromises();

      xhrInstances[0].triggerLoad(500, "Internal Server Error");
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("shows error on network failure", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await flushPromises();

      xhrInstances[0].triggerError();
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("shows error on upload abort", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await flushPromises();

      xhrInstances[0].triggerAbort();
      await flushPromises();

      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("hides progress bar after failed upload", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await flushPromises();
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
      await flushPromises();
      xhrInstances[0].triggerLoad(500, "error");
      await flushPromises();

      // Retry button should appear
      const retryBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("retry"));
      expect(retryBtn).toBeDefined();

      // Click retry
      await retryBtn!.trigger("click");
      await flushPromises();

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
    it("does not set Authorization header on XHR", async () => {
      const wrapper = mountPage();
      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });

      emitUploader(wrapper, mockFile);
      await flushPromises();

      expect(xhrInstances[0].setRequestHeader).not.toHaveBeenCalled();
    });
  });

  describe("pre-upload token refresh", () => {
    it("calls ensureFreshToken before creating XHR", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      const spy = vi.spyOn(authStore, "ensureFreshToken").mockResolvedValue(true);

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      expect(spy).toHaveBeenCalled();
      // XHR should still be created after ensureFreshToken
      expect(xhrInstances.length).toBe(1);
      expect(xhrInstances[0].send).toHaveBeenCalled();
      spy.mockRestore();
    });

    it("proceeds with upload even when ensureFreshToken returns false", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      // ensureFreshToken returns false (no tokens) but upload should still attempt
      const spy = vi.spyOn(authStore, "ensureFreshToken").mockResolvedValue(false);

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // XHR is still created — the server will reject it with 401
      expect(xhrInstances.length).toBe(1);
      expect(xhrInstances[0].send).toHaveBeenCalled();
      spy.mockRestore();
    });
  });

  describe("401 retry logic", () => {
    it("retries upload after refreshing token on 401 response", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      const refreshSpy = vi.spyOn(authStore, "refreshAccessToken").mockImplementation(async () => {
        authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      });

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // First XHR returns 401
      expect(xhrInstances.length).toBe(1);
      xhrInstances[0].triggerLoad(401, JSON.stringify({ detail: "Given token not valid for any token type" }));
      await flushPromises();

      // Should have called refreshAccessToken and created a second XHR
      expect(refreshSpy).toHaveBeenCalled();
      expect(xhrInstances.length).toBe(2);
      expect(xhrInstances[1].send).toHaveBeenCalled();

      // Complete the retry successfully
      xhrInstances[1].triggerLoad(200);
      await flushPromises();

      expect(notifyMock.success).toHaveBeenCalled();
      expect(wrapper.find(".message").exists()).toBe(false);
      refreshSpy.mockRestore();
    });

    it("shows error when retry also fails with non-401", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      vi.spyOn(authStore, "refreshAccessToken").mockImplementation(async () => {
        authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      });

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // First XHR returns 401
      xhrInstances[0].triggerLoad(401, JSON.stringify({ detail: "Token expired" }));
      await flushPromises();

      // Retry XHR returns 500
      xhrInstances[1].triggerLoad(500, JSON.stringify({ detail: "Server error" }));
      await flushPromises();

      // Should show error
      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("shows error when token refresh fails during 401 retry", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      // refreshAccessToken fails → clears tokenExpiry
      vi.spyOn(authStore, "refreshAccessToken").mockImplementation(async () => {
        authStore.tokenExpiry = null;
      });

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // First XHR returns 401
      xhrInstances[0].triggerLoad(401, JSON.stringify({ detail: "Token expired" }));
      await flushPromises();

      // Refresh failed — should show original error, no retry XHR created
      expect(xhrInstances.length).toBe(1);
      expect(wrapper.find(".message").exists()).toBe(true);
    });

    it("does not retry on non-401 errors", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      const refreshSpy = vi.spyOn(authStore, "refreshAccessToken");

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // 400 Bad Request — should NOT trigger retry
      xhrInstances[0].triggerLoad(400, JSON.stringify({ detail: "Invalid CSV" }));
      await flushPromises();

      expect(refreshSpy).not.toHaveBeenCalled();
      expect(xhrInstances.length).toBe(1);
      expect(wrapper.find(".message").exists()).toBe(true);
      expect(wrapper.text()).toContain("Invalid CSV");
      refreshSpy.mockRestore();
    });

    it("does not retry on 500 errors", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      const refreshSpy = vi.spyOn(authStore, "refreshAccessToken");

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      xhrInstances[0].triggerLoad(500, "Internal Server Error");
      await flushPromises();

      expect(refreshSpy).not.toHaveBeenCalled();
      expect(xhrInstances.length).toBe(1);
      refreshSpy.mockRestore();
    });

    it("resets progress to 0 before retry upload", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      vi.spyOn(authStore, "refreshAccessToken").mockImplementation(async () => {
        authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      });

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // Simulate progress reaching 80%
      xhrInstances[0].triggerUploadProgress(80, 100);
      await nextTick();
      expect(wrapper.find(".progress-bar").attributes("data-value")).toBe("80");

      // 401 triggers retry
      xhrInstances[0].triggerLoad(401, JSON.stringify({ detail: "Token expired" }));
      await flushPromises();

      // Progress should be reset to 0 for the retry
      expect(wrapper.find(".progress-bar").attributes("data-value")).toBe("0");
    });

    it("hides progress bar after successful retry", async () => {
      vi.useFakeTimers();
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 60;

      vi.spyOn(authStore, "refreshAccessToken").mockImplementation(async () => {
        authStore.tokenExpiry = Math.floor(Date.now() / 1000) + 300;
      });

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // 401 → retry
      xhrInstances[0].triggerLoad(401, JSON.stringify({ detail: "Token expired" }));
      await flushPromises();

      // Retry succeeds
      xhrInstances[1].triggerLoad(200);
      await flushPromises();

      expect(wrapper.find(".progress-bar").exists()).toBe(false);
      expect(notifyMock.success).toHaveBeenCalled();
      vi.advanceTimersByTime(500);
      expect(mockPush).toHaveBeenCalledWith("/projects/1/data");

      vi.useRealTimers();
    });
  });

  describe("error status property", () => {
    it("attaches HTTP status to error on non-2xx response", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      // Spy on refreshAccessToken to verify 401 detection works via status
      const refreshSpy = vi.spyOn(authStore, "refreshAccessToken").mockImplementation(async () => {
        authStore.tokenExpiry = null;
      });

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // 401 should be detected by the retry logic (proves .status is set)
      xhrInstances[0].triggerLoad(401, JSON.stringify({ detail: "Unauthorized" }));
      await flushPromises();

      expect(refreshSpy).toHaveBeenCalled();
      refreshSpy.mockRestore();
    });

    it("does not trigger 401 retry for 403 forbidden", async () => {
      const wrapper = mountPage();
      const { useAuthStore } = await import("@/stores/auth");
      const authStore = useAuthStore();
      const refreshSpy = vi.spyOn(authStore, "refreshAccessToken");

      const mockFile = new File(["test"], "test.csv", { type: "text/csv" });
      emitUploader(wrapper, mockFile);
      await flushPromises();

      // 403 is different from 401
      xhrInstances[0].triggerLoad(403, JSON.stringify({ detail: "Forbidden" }));
      await flushPromises();

      expect(refreshSpy).not.toHaveBeenCalled();
      expect(xhrInstances.length).toBe(1);
      expect(wrapper.text()).toContain("Forbidden");
      refreshSpy.mockRestore();
    });
  });
});
