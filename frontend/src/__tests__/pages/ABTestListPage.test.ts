import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ABTestListPage from "@/pages/ABTestListPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const pushMock = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
  useRoute: () => ({ params: { projectId: "1" } }),
  RouterLink: { template: '<a><slot /></a>' },
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
  unwrapResults: (res: any) => (Array.isArray(res) ? res : res.results),
}));

const mockGetTests = vi.fn();
const mockCreateTest = vi.fn();
const mockGetSlots = vi.fn();

vi.mock("@/api/production", () => ({
  getABTests: (...args: unknown[]) => mockGetTests(...args),
  createABTest: (...args: unknown[]) => mockCreateTest(...args),
  getDeploymentSlots: (...args: unknown[]) => mockGetSlots(...args),
}));

const sampleTests = [
  { id: 1, name: "Test 1", status: "DRAFT", control_slot: 1, variant_slot: 2, target_metric_name: "ctr", started_at: null },
  { id: 2, name: "Test 2", status: "RUNNING", control_slot: 3, variant_slot: 4, target_metric_name: "revenue", started_at: "2025-01-01T00:00:00Z" },
];

function mountPage() {
  return mount(ABTestListPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: { template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        Tag: { template: '<span class="tag">{{ $attrs.value }}</span>', inheritAttrs: false },
        EmptyState: { template: '<div class="empty-state">{{ title }} {{ description }}<slot /></div>', props: ["icon", "title", "description"] },
        DataTable: { template: '<div class="datatable"><slot /></div>' },
        Column: { template: '<div class="column"><slot /></div>' },
        Dialog: { template: '<div class="dialog" v-if="$attrs.visible"><slot /><slot name="footer" /></div>', inheritAttrs: false },
        InputText: { template: '<input />', inheritAttrs: false },
        Select: { template: '<select />', inheritAttrs: false },
      },
    },
  });
}

describe("ABTestListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    mockGetSlots.mockResolvedValue({ results: [{ id: 1, name: "Slot A" }, { id: 2, name: "Slot B" }] });
  });

  it("renders loading state initially", async () => {
    mockGetTests.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders tests on success", async () => {
    mockGetTests.mockResolvedValue({ results: sampleTests });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".datatable").exists()).toBe(true);
    expect(wrapper.text()).toContain("A/B Tests");
  });

  it("renders empty state when no tests", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No A/B tests");
  });

  it("renders error with retry on failure", async () => {
    mockGetTests.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Network error");
  });

  it("ignores AbortError", async () => {
    mockGetTests.mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("retries on retry button click", async () => {
    mockGetTests
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValueOnce({ results: sampleTests });
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(mockGetTests).toHaveBeenCalledTimes(2);
  });

  it("renders page title", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find("h2").text()).toContain("A/B Tests");
  });

  it("has New Test button", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("New Test");
  });

  it("does not show empty state when error", async () => {
    mockGetTests.mockRejectedValueOnce(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(false);
  });

  it("fetches deployment slots on mount", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    mountPage();
    await flushPromises();
    expect(mockGetSlots).toHaveBeenCalled();
  });

  it("handles slot fetch error gracefully", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    mockGetSlots.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    // Should still render without crashing
    expect(wrapper.text()).toContain("A/B Tests");
  });

  it("handleCreate creates a test and refreshes", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    mockCreateTest.mockResolvedValue({ id: 3, name: "New Test" });
    const wrapper = mountPage();
    await flushPromises();

    // Set form data and call handleCreate via vm
    const vm = wrapper.vm as any;
    vm.createForm.name = "New Test";
    vm.createForm.control_slot = 1;
    vm.createForm.variant_slot = 2;
    vm.showCreateDialog = true;
    await nextTick();

    await vm.handleCreate();
    await flushPromises();

    expect(mockCreateTest).toHaveBeenCalled();
    expect(notifySuccess).toHaveBeenCalledWith("A/B test created.");
    expect(vm.showCreateDialog).toBe(false);
  });

  it("handleCreate shows error on failure", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    mockCreateTest.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.name = "New Test";
    vm.createForm.control_slot = 1;
    vm.createForm.variant_slot = 2;
    await vm.handleCreate();
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to create A/B test.");
  });

  it("handleCreate early returns if no slots selected", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.control_slot = null;
    vm.createForm.variant_slot = null;
    await vm.handleCreate();
    await flushPromises();

    expect(mockCreateTest).not.toHaveBeenCalled();
  });

  it("statusSeverity returns correct values", async () => {
    mockGetTests.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    expect(vm.statusSeverity("DRAFT")).toBe("info");
    expect(vm.statusSeverity("RUNNING")).toBe("warn");
    expect(vm.statusSeverity("COMPLETED")).toBe("success");
    expect(vm.statusSeverity("CANCELLED")).toBe("danger");
    expect(vm.statusSeverity("UNKNOWN")).toBe("secondary");
  });
});
