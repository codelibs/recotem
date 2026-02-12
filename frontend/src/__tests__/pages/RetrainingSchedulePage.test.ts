import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import RetrainingSchedulePage from "@/pages/RetrainingSchedulePage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

const notifySuccess = vi.fn();
const notifyError = vi.fn();
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({ success: notifySuccess, error: notifyError }),
}));

const apiMock = vi.fn();
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  classifyApiError: (err: any) => ({ kind: "unknown", message: err?.message ?? "Unknown error" }),
  unwrapResults: (res: any) => (Array.isArray(res) ? res : res.results),
}));

const mockGetSchedules = vi.fn();
const mockCreateSchedule = vi.fn();
const mockUpdateSchedule = vi.fn();
const mockTrigger = vi.fn();
const mockGetRuns = vi.fn();

vi.mock("@/api/production", () => ({
  getRetrainingSchedules: (...args: unknown[]) => mockGetSchedules(...args),
  createRetrainingSchedule: (...args: unknown[]) => mockCreateSchedule(...args),
  updateRetrainingSchedule: (...args: unknown[]) => mockUpdateSchedule(...args),
  triggerRetraining: (...args: unknown[]) => mockTrigger(...args),
  getRetrainingRuns: (...args: unknown[]) => mockGetRuns(...args),
}));

const sampleSchedule = {
  id: 1,
  project: 1,
  is_enabled: true,
  cron_expression: "0 2 * * *",
  training_data: null,
  model_configuration: null,
  retune: false,
  split_config: null,
  evaluation_config: null,
  max_retries: 3,
  notify_on_failure: true,
  last_run_at: "2025-01-01T00:00:00Z",
  last_run_status: "SUCCESS",
  next_run_at: "2025-01-02T02:00:00Z",
  auto_deploy: false,
  ins_datetime: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
};

const sampleRuns = [
  { id: 1, schedule: 1, status: "SUCCESS", trained_model: 5, error_message: null, ins_datetime: "2025-01-01T00:00:00Z", completed_at: "2025-01-01T01:00:00Z" },
  { id: 2, schedule: 1, status: "FAILED", trained_model: null, error_message: "Timeout", ins_datetime: "2025-01-02T02:00:00Z", completed_at: "2025-01-02T02:30:00Z" },
];

function mountPage() {
  return mount(RetrainingSchedulePage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: { template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        Tag: { template: '<span class="tag">{{ $attrs.value }}</span>', inheritAttrs: false },
        EmptyState: { template: '<div class="empty-state">{{ title }}<slot /></div>', props: ["icon", "title", "description"] },
        DataTable: { template: '<div class="datatable"><slot /></div>' },
        Column: { template: '<div class="column"><slot /></div>' },
        Dialog: { template: '<div class="dialog" v-if="$attrs.visible"><slot /><slot name="footer" /></div>', inheritAttrs: false },
        InputText: { template: '<input />', inheritAttrs: false },
        InputSwitch: { template: '<input type="checkbox" />', inheritAttrs: false },
        Select: { template: '<select />', inheritAttrs: false },
      },
    },
  });
}

describe("RetrainingSchedulePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    apiMock.mockResolvedValue({ results: [] }); // dropdown data
  });

  it("renders loading state initially", async () => {
    mockGetSchedules.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders schedule config on success", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: sampleRuns });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Configuration");
    expect(wrapper.text()).toContain("Trigger Now");
    expect(wrapper.text()).toContain("Recent Runs");
  });

  it("renders empty state when no schedule", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No retraining schedule");
  });

  it("renders error with retry on failure", async () => {
    mockGetSchedules.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Network error");
  });

  it("ignores AbortError", async () => {
    mockGetSchedules.mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("retries on retry button click", async () => {
    mockGetSchedules
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValueOnce({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(mockGetSchedules).toHaveBeenCalledTimes(2);
  });

  it("renders page title", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find("h2").text()).toContain("Retraining Schedule");
  });

  it("shows Create Schedule button when no schedule", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Create Schedule");
  });

  it("does not show empty state when error", async () => {
    mockGetSchedules.mockRejectedValueOnce(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(false);
  });

  it("hides skeleton after load", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("fetches dropdown data on mount", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    mountPage();
    await flushPromises();
    expect(apiMock).toHaveBeenCalled();
  });

  it("shows next run and last run info", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: sampleRuns });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Next run:");
    expect(wrapper.text()).toContain("Last run:");
  });

  it("shows no scheduled run when next_run_at is null", async () => {
    mockGetSchedules.mockResolvedValue({ results: [{ ...sampleSchedule, next_run_at: null, last_run_at: null, last_run_status: null }] });
    mockGetRuns.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("No scheduled run");
  });

  it("shows runs empty state when no runs", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("No runs yet");
  });

  it("shows runs datatable when runs exist", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: sampleRuns });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".datatable").exists()).toBe(true);
  });

  it("handles trigger action", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    mockTrigger.mockResolvedValue({});
    const wrapper = mountPage();
    await flushPromises();

    const triggerBtn = wrapper.findAll("button").find(b => b.text().includes("Trigger Now"));
    await triggerBtn!.trigger("click");
    await flushPromises();

    expect(mockTrigger).toHaveBeenCalledWith(1);
    expect(notifySuccess).toHaveBeenCalledWith("Retraining triggered.");
  });

  it("handles trigger action failure", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    mockTrigger.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const triggerBtn = wrapper.findAll("button").find(b => b.text().includes("Trigger Now"));
    await triggerBtn!.trigger("click");
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to trigger retraining.");
  });

  it("handles dropdown data fetch failure gracefully", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    apiMock.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    // Should render without crashing
    expect(wrapper.text()).toContain("Retraining Schedule");
  });

  it("fetches runs when schedule exists", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: sampleRuns });
    mountPage();
    await flushPromises();
    expect(mockGetRuns).toHaveBeenCalledWith(1, expect.anything());
  });

  it("does not fetch runs when no schedule", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    mountPage();
    await flushPromises();
    expect(mockGetRuns).not.toHaveBeenCalled();
  });

  it("updateSchedule updates and notifies success", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    mockUpdateSchedule.mockResolvedValue({ ...sampleSchedule, is_enabled: false });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.updateSchedule({ is_enabled: false });
    await flushPromises();

    expect(mockUpdateSchedule).toHaveBeenCalledWith(1, { is_enabled: false });
    expect(notifySuccess).toHaveBeenCalledWith("Schedule updated.");
  });

  it("updateSchedule shows error on failure", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    mockUpdateSchedule.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.updateSchedule({ is_enabled: false });
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to update schedule.");
  });

  it("updateSchedule early returns if no schedule", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.updateSchedule({ is_enabled: false });
    expect(mockUpdateSchedule).not.toHaveBeenCalled();
  });

  it("saveField calls updateSchedule with field value", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    mockUpdateSchedule.mockResolvedValue({ ...sampleSchedule, cron_expression: "0 3 * * *" });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.editForm.cron_expression = "0 3 * * *";
    await vm.saveField("cron_expression");
    await flushPromises();

    expect(mockUpdateSchedule).toHaveBeenCalledWith(1, { cron_expression: "0 3 * * *" });
  });

  it("toggleEnabled calls updateSchedule", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockResolvedValue({ results: [] });
    mockUpdateSchedule.mockResolvedValue({ ...sampleSchedule, is_enabled: false });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.toggleEnabled(false);
    await flushPromises();

    expect(mockUpdateSchedule).toHaveBeenCalledWith(1, { is_enabled: false });
  });

  it("handleTrigger early returns if no schedule", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.handleTrigger();
    expect(mockTrigger).not.toHaveBeenCalled();
  });

  it("handleCreateSchedule creates schedule and notifies", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    mockCreateSchedule.mockResolvedValue({ ...sampleSchedule, id: 2 });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.newForm.cron_expression = "0 3 * * *";
    vm.newForm.retune = true;
    vm.newForm.auto_deploy = true;
    vm.showCreateDialog = true;
    await vm.handleCreateSchedule();
    await flushPromises();

    expect(mockCreateSchedule).toHaveBeenCalled();
    expect(notifySuccess).toHaveBeenCalledWith("Schedule created.");
    expect(vm.showCreateDialog).toBe(false);
    expect(vm.schedule).not.toBeNull();
  });

  it("handleCreateSchedule shows error on failure", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    mockCreateSchedule.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.newForm.cron_expression = "0 3 * * *";
    await vm.handleCreateSchedule();
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to create schedule.");
  });

  it("runStatusSeverity returns correct values", async () => {
    mockGetSchedules.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    expect(vm.runStatusSeverity("SUCCESS")).toBe("success");
    expect(vm.runStatusSeverity("FAILED")).toBe("danger");
    expect(vm.runStatusSeverity("RUNNING")).toBe("info");
    expect(vm.runStatusSeverity("UNKNOWN")).toBe("secondary");
  });

  it("handles runs fetch error gracefully", async () => {
    mockGetSchedules.mockResolvedValue({ results: [sampleSchedule] });
    mockGetRuns.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    // Should not crash, runs will be empty
    const vm = wrapper.vm as any;
    expect(vm.runs.length).toBe(0);
  });
});
