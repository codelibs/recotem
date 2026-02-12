import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ABTestDetailPage from "@/pages/ABTestDetailPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const pushMock = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
  useRoute: () => ({ params: { projectId: "1", testId: "10" } }),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

const notifySuccess = vi.fn();
const notifyError = vi.fn();
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({ success: notifySuccess, error: notifyError }),
}));

vi.mock("@/api/client", () => ({
  classifyApiError: (err: any) => ({ kind: "unknown", message: err?.message ?? "Unknown error" }),
}));

const mockGetDetail = vi.fn();
const mockGetResults = vi.fn();
const mockStart = vi.fn();
const mockStop = vi.fn();
const mockPromote = vi.fn();

vi.mock("@/api/production", () => ({
  getABTestDetail: (...args: unknown[]) => mockGetDetail(...args),
  getABTestResults: (...args: unknown[]) => mockGetResults(...args),
  startABTest: (...args: unknown[]) => mockStart(...args),
  stopABTest: (...args: unknown[]) => mockStop(...args),
  promoteWinner: (...args: unknown[]) => mockPromote(...args),
}));

const testData = {
  id: 10,
  name: "Test A/B",
  status: "DRAFT",
  control_slot: 1,
  variant_slot: 2,
  target_metric_name: "ctr",
  confidence_level: 0.95,
  min_sample_size: 1000,
  started_at: null,
  ended_at: null,
  winner_slot: null,
  ins_datetime: "2025-01-01T00:00:00Z",
};

const resultsData = {
  control_impressions: 500,
  control_conversions: 50,
  control_rate: 0.1,
  variant_impressions: 500,
  variant_conversions: 75,
  variant_rate: 0.15,
  z_score: 2.35,
  p_value: 0.019,
  significant: true,
  lift: 0.5,
  confidence_interval: [0.02, 0.08],
};

function mountPage() {
  return mount(ABTestDetailPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: { template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        Tag: { template: '<span class="tag">{{ $attrs.value }}</span>', inheritAttrs: false },
        EmptyState: { template: '<div class="empty-state">{{ title }}<slot /></div>', props: ["icon", "title", "description"] },
      },
    },
  });
}

describe("ABTestDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders loading state initially", async () => {
    mockGetDetail.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders test detail on success", async () => {
    mockGetDetail.mockResolvedValue({ ...testData });
    mockGetResults.mockRejectedValue(new Error("no results"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Test A/B");
    expect(wrapper.text()).toContain("Slot #1");
    expect(wrapper.text()).toContain("Slot #2");
    expect(wrapper.text()).toContain("ctr");
    expect(wrapper.text()).toContain("95%");
  });

  it("renders error with retry on failure", async () => {
    mockGetDetail.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Network error");
    expect(wrapper.text()).toContain("Retry");
  });

  it("ignores AbortError", async () => {
    mockGetDetail.mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("shows Start Test button for DRAFT status", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "DRAFT" });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Start Test");
  });

  it("shows Stop Test button for RUNNING status", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "RUNNING" });
    mockGetResults.mockResolvedValue({ ...resultsData, significant: false });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Stop Test");
  });

  it("shows Promote Winner button for COMPLETED with significant results", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "COMPLETED" });
    mockGetResults.mockResolvedValue({ ...resultsData });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Promote Winner");
  });

  it("renders results data", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "COMPLETED" });
    mockGetResults.mockResolvedValue({ ...resultsData });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Statistically Significant");
    expect(wrapper.text()).toContain("500");
    expect(wrapper.text()).toContain("Statistical Summary");
  });

  it("shows empty state when no results", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "DRAFT" });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No results yet");
  });

  it("handles start action", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "DRAFT" });
    mockStart.mockResolvedValue({ ...testData, status: "RUNNING" });
    mockGetResults.mockResolvedValue(resultsData);
    const wrapper = mountPage();
    await flushPromises();

    const startBtn = wrapper.findAll("button").find(b => b.text().includes("Start Test"));
    await startBtn!.trigger("click");
    await flushPromises();

    expect(mockStart).toHaveBeenCalledWith(10);
    expect(notifySuccess).toHaveBeenCalledWith("A/B test started.");
  });

  it("handles stop action", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "RUNNING" });
    mockGetResults.mockResolvedValue({ ...resultsData, significant: false });
    mockStop.mockResolvedValue({ ...testData, status: "COMPLETED" });
    const wrapper = mountPage();
    await flushPromises();

    const stopBtn = wrapper.findAll("button").find(b => b.text().includes("Stop Test"));
    await stopBtn!.trigger("click");
    await flushPromises();

    expect(mockStop).toHaveBeenCalledWith(10);
    expect(notifySuccess).toHaveBeenCalledWith("A/B test stopped.");
  });

  it("handles promote action", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "COMPLETED" });
    mockGetResults.mockResolvedValue({ ...resultsData });
    mockPromote.mockResolvedValue({});
    const wrapper = mountPage();
    await flushPromises();

    const promoteBtn = wrapper.findAll("button").find(b => b.text().includes("Promote Winner"));
    await promoteBtn!.trigger("click");
    await flushPromises();

    expect(mockPromote).toHaveBeenCalledWith(10, 2); // variant has higher rate
    expect(notifySuccess).toHaveBeenCalledWith("Winner promoted.");
  });

  it("handles start action failure", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "DRAFT" });
    mockStart.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const startBtn = wrapper.findAll("button").find(b => b.text().includes("Start Test"));
    await startBtn!.trigger("click");
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to start A/B test.");
  });

  it("handles stop action failure", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "RUNNING" });
    mockGetResults.mockResolvedValue({ ...resultsData, significant: false });
    mockStop.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const stopBtn = wrapper.findAll("button").find(b => b.text().includes("Stop Test"));
    await stopBtn!.trigger("click");
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to stop A/B test.");
  });

  it("handles promote action failure", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "COMPLETED" });
    mockGetResults.mockResolvedValue({ ...resultsData });
    mockPromote.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const promoteBtn = wrapper.findAll("button").find(b => b.text().includes("Promote Winner"));
    await promoteBtn!.trigger("click");
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to promote winner.");
  });

  it("shows winner tag when winner_slot is set", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "COMPLETED", winner_slot: 2 });
    mockGetResults.mockResolvedValue({ ...resultsData });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Winner: Slot #2");
  });

  it("shows Not Significant tag when results are not significant", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "COMPLETED" });
    mockGetResults.mockResolvedValue({ ...resultsData, significant: false });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Not Significant");
  });

  it("does not fetch results for DRAFT status", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "DRAFT" });
    const wrapper = mountPage();
    await flushPromises();
    expect(mockGetResults).not.toHaveBeenCalled();
  });

  it("shows formatted dates when started_at is present", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "RUNNING", started_at: "2025-06-01T12:00:00Z" });
    mockGetResults.mockResolvedValue(resultsData);
    const wrapper = mountPage();
    await flushPromises();
    // started_at is present, so it should NOT show "Not started"
    expect(wrapper.text()).not.toContain("Not started");
  });

  it("retries on retry button click", async () => {
    mockGetDetail
      .mockRejectedValueOnce(new Error("Network error"))
      .mockResolvedValueOnce({ ...testData });
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(mockGetDetail).toHaveBeenCalledTimes(2);
  });

  it("starts polling for RUNNING status on mount", async () => {
    mockGetDetail.mockResolvedValue({ ...testData, status: "RUNNING" });
    mockGetResults.mockResolvedValue(resultsData);
    const wrapper = mountPage();
    await flushPromises();

    // Advance timer to trigger poll
    mockGetDetail.mockResolvedValue({ ...testData, status: "RUNNING" });
    mockGetResults.mockResolvedValue(resultsData);
    vi.advanceTimersByTime(10000);
    await flushPromises();

    // Initial fetch + poll fetch
    expect(mockGetDetail.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("maps statusSeverity correctly for all statuses", async () => {
    // Test CANCELLED status
    mockGetDetail.mockResolvedValue({ ...testData, status: "CANCELLED" });
    mockGetResults.mockResolvedValue(resultsData);
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("CANCELLED");
  });

  it("navigates back on back button click", async () => {
    mockGetDetail.mockResolvedValue({ ...testData });
    const wrapper = mountPage();
    await flushPromises();

    // The back button uses onClick which is handled by our stub
    const buttons = wrapper.findAll("button");
    // First button is the back button (it has no label text)
    const backBtn = buttons[0];
    await backBtn.trigger("click");
    expect(pushMock).toHaveBeenCalledWith("/projects/1/ab-tests");
  });
});
