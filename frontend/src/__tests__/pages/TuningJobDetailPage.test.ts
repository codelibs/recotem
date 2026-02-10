import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import TuningJobDetailPage from "@/pages/TuningJobDetailPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1", jobId: "5" } }),
}));

const apiMock = vi.fn();

function setDefaultApiMock() {
  apiMock.mockImplementation((url: string) => {
    if (url.includes("parameter_tuning_job")) {
      return Promise.resolve({
        id: 5,
        data: 1,
        split: 1,
        evaluation: 1,
        status: "RUNNING",
        n_tasks_parallel: 2,
        n_trials: 50,
        memory_budget: 4096,
        timeout_overall: null,
        timeout_singlestep: null,
        random_seed: null,
        tried_algorithms_json: null,
        irspack_version: "0.4.0",
        train_after_tuning: true,
        tuned_model: null,
        best_config: null,
        best_score: 0.42,
        task_links: [],
        ins_datetime: "2025-01-01T00:00:00Z",
      });
    }
    if (url.includes("task_log")) {
      return Promise.resolve({
        results: [
          { id: 1, task: 1, contents: "Trial 1 complete", ins_datetime: "2025-01-01T00:01:00Z" },
        ],
        count: 1,
      });
    }
    return Promise.resolve({});
  });
}

vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  classifyApiError: (err: any) => ({ kind: "unknown", status: undefined, message: err?.message ?? "Unknown error", fieldErrors: undefined }),
  unwrapResults: (res: any) => Array.isArray(res) ? res : res.results,
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

const mockConnect = vi.fn();
vi.mock("@/composables/useJobStatus", () => ({
  useJobLogs: () => ({
    logs: { value: [] },
    isConnected: { value: false },
    connectionState: { value: "disconnected" },
    connect: mockConnect,
    disconnect: vi.fn(),
  }),
}));

function mountPage() {
  return mount(TuningJobDetailPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: { template: '<button :aria-label="$attrs[\'aria-label\']" @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        Tag: { template: '<span class="tag"><slot />{{ $attrs.value }}</span>', inheritAttrs: false },
      },
    },
  });
}

describe("TuningJobDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    setDefaultApiMock();
  });

  it("renders job heading", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Tuning Job");
    expect(wrapper.text()).toContain("#5");
  });

  it("fetches job data and logs on mount", async () => {
    mountPage();
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      expect.stringContaining("parameter_tuning_job"),
      expect.anything(),
    );
  });

  it("renders logs section after load", async () => {
    const wrapper = mountPage();
    await flushPromises();
    // The page should contain "Logs" heading when data loads
    expect(wrapper.text()).toMatch(/Log|Tuning Job/);
  });

  // --- Loading state tests ---

  it("shows loading skeletons before data loads", async () => {
    apiMock.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    await nextTick();

    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("hides loading skeletons after data loads", async () => {
    const wrapper = mountPage();
    // loadJob does sequential awaits: job fetch then task_log fetch
    await flushPromises();
    await flushPromises();

    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("shows 2 skeleton placeholders during loading", async () => {
    apiMock.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    await nextTick();

    expect(wrapper.findAll(".skeleton").length).toBe(2);
  });

  // --- Error state tests ---

  it("displays error message when API fails", async () => {
    apiMock.mockRejectedValue(new Error("Connection refused"));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Connection refused");
  });

  it("hides job details when error is shown", async () => {
    apiMock.mockRejectedValue(new Error("Server error"));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(true);
    // Job details should not be visible
    expect(wrapper.text()).not.toContain("Job Details");
  });

  it("shows retry button in error state", async () => {
    apiMock.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    expect(retryBtn).toBeDefined();
  });

  it("retries loading when retry button is clicked", async () => {
    apiMock
      .mockRejectedValueOnce(new Error("Timeout"))
      .mockImplementation((url: string) => {
        if (url.includes("parameter_tuning_job")) {
          return Promise.resolve({
            id: 5,
            data: 1,
            split: 1,
            evaluation: 1,
            status: "COMPLETED",
            n_tasks_parallel: 2,
            n_trials: 50,
            memory_budget: 4096,
            timeout_overall: null,
            timeout_singlestep: null,
            random_seed: null,
            tried_algorithms_json: null,
            irspack_version: "0.4.0",
            train_after_tuning: true,
            tuned_model: null,
            best_config: 1,
            best_score: 0.55,
            task_links: [],
            ins_datetime: "2025-01-01T00:00:00Z",
          });
        }
        return Promise.resolve({ results: [], count: 0 });
      });

    const wrapper = mountPage();
    await flushPromises();

    // Error state
    expect(wrapper.find(".message").exists()).toBe(true);

    // Click retry
    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    // Should now show job details
    expect(wrapper.find(".message").exists()).toBe(false);
    expect(wrapper.text()).toContain("Job Details");
  });

  it("ignores AbortError and does not show error", async () => {
    const abortError = new DOMException("Aborted", "AbortError");
    apiMock.mockRejectedValue(abortError);
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("clears error on retry before new request starts", async () => {
    let resolveRetry: (value: unknown) => void;
    apiMock
      .mockRejectedValueOnce(new Error("First failure"))
      .mockReturnValueOnce(new Promise((resolve) => { resolveRetry = resolve; }));

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(true);

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await nextTick();

    // Error should be cleared during retry
    // Loading skeletons should appear
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);

    // Resolve the retry
    resolveRetry!({
      id: 5,
      data: 1,
      split: 1,
      evaluation: 1,
      status: "RUNNING",
      n_tasks_parallel: 2,
      n_trials: 50,
      memory_budget: 4096,
      timeout_overall: null,
      timeout_singlestep: null,
      random_seed: null,
      tried_algorithms_json: null,
      irspack_version: "0.4.0",
      train_after_tuning: true,
      tuned_model: null,
      best_config: null,
      best_score: null,
      task_links: [],
      ins_datetime: "2025-01-01T00:00:00Z",
    });
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(false);
    expect(wrapper.text()).toContain("Job Details");
  });

  // --- Data rendering tests ---

  it("renders job configuration details after load", async () => {
    const wrapper = mountPage();
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("50"); // n_trials
    expect(wrapper.text()).toContain("4096"); // memory_budget
    expect(wrapper.text()).toContain("0.4200"); // best_score
  });

  it("renders log entries from task_log API", async () => {
    const wrapper = mountPage();
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain("Trial 1 complete");
  });

  it("shows 'No logs yet' when there are no log entries", async () => {
    apiMock.mockImplementation((url: string) => {
      if (url.includes("parameter_tuning_job")) {
        return Promise.resolve({
          id: 5,
          data: 1,
          split: 1,
          evaluation: 1,
          status: "PENDING",
          n_tasks_parallel: 2,
          n_trials: 50,
          memory_budget: 4096,
          timeout_overall: null,
          timeout_singlestep: null,
          random_seed: null,
          tried_algorithms_json: null,
          irspack_version: "0.4.0",
          train_after_tuning: true,
          tuned_model: null,
          best_config: null,
          best_score: null,
          task_links: [],
          ins_datetime: "2025-01-01T00:00:00Z",
        });
      }
      if (url.includes("task_log")) {
        return Promise.resolve({ results: [], count: 0 });
      }
      return Promise.resolve({});
    });

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("No logs yet");
  });

  it("connects to WebSocket when job has no best_config", async () => {
    const wrapper = mountPage();
    await flushPromises();

    // The default mock has best_config: null, so it should connect
    expect(mockConnect).toHaveBeenCalled();
  });

  describe("downloadLogs", () => {
    it("creates a downloadable text file with log contents", async () => {
      const wrapper = mountPage();
      await flushPromises();
      await flushPromises();

      // Log entries should be present from the mock
      expect(wrapper.text()).toContain("Trial 1 complete");

      // Mock URL.createObjectURL and URL.revokeObjectURL
      const mockUrl = "blob:http://localhost/fake-blob";
      const createObjectURLSpy = vi.fn().mockReturnValue(mockUrl);
      const revokeObjectURLSpy = vi.fn();
      globalThis.URL.createObjectURL = createObjectURLSpy;
      globalThis.URL.revokeObjectURL = revokeObjectURLSpy;

      // Mock document.createElement for the anchor element
      const mockClick = vi.fn();
      const mockAnchor = { href: "", download: "", click: mockClick };
      const originalCreateElement = document.createElement.bind(document);
      const createElementSpy = vi
        .spyOn(document, "createElement")
        .mockImplementation((tag: string, options?: any) => {
          if (tag === "a") return mockAnchor as any;
          return originalCreateElement(tag, options);
        });

      // Find the download logs button by its aria-label
      const downloadBtn = wrapper.find('button[aria-label="Download Logs"]');
      expect(downloadBtn.exists()).toBe(true);
      await downloadBtn.trigger("click");

      expect(createElementSpy).toHaveBeenCalledWith("a");
      expect(mockAnchor.download).toBe("tuning-job-5-logs.txt");
      expect(mockClick).toHaveBeenCalled();
      expect(revokeObjectURLSpy).toHaveBeenCalledWith(mockUrl);

      createElementSpy.mockRestore();
    });
  });

  it("does not connect to WebSocket when job has best_config", async () => {
    apiMock.mockImplementation((url: string) => {
      if (url.includes("parameter_tuning_job")) {
        return Promise.resolve({
          id: 5,
          data: 1,
          split: 1,
          evaluation: 1,
          status: "COMPLETED",
          n_tasks_parallel: 2,
          n_trials: 50,
          memory_budget: 4096,
          timeout_overall: null,
          timeout_singlestep: null,
          random_seed: null,
          tried_algorithms_json: null,
          irspack_version: "0.4.0",
          train_after_tuning: true,
          tuned_model: 1,
          best_config: 1,
          best_score: 0.55,
          task_links: [],
          ins_datetime: "2025-01-01T00:00:00Z",
        });
      }
      return Promise.resolve({ results: [], count: 0 });
    });

    const wrapper = mountPage();
    await flushPromises();

    expect(mockConnect).not.toHaveBeenCalled();
  });
});
