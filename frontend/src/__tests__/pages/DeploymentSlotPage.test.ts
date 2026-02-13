import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import DeploymentSlotPage from "@/pages/DeploymentSlotPage.vue";
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

const mockGetSlots = vi.fn();
const mockCreateSlot = vi.fn();
const mockUpdateSlot = vi.fn();
const mockDeleteSlot = vi.fn();

vi.mock("@/api/production", () => ({
  getDeploymentSlots: (...args: unknown[]) => mockGetSlots(...args),
  createDeploymentSlot: (...args: unknown[]) => mockCreateSlot(...args),
  updateDeploymentSlot: (...args: unknown[]) => mockUpdateSlot(...args),
  deleteDeploymentSlot: (...args: unknown[]) => mockDeleteSlot(...args),
}));

const sampleSlots = [
  { id: 1, name: "Primary", trained_model: 10, weight: 70, is_active: true, updated_at: "2025-01-01T00:00:00Z" },
  { id: 2, name: "Canary", trained_model: 11, weight: 30, is_active: true, updated_at: "2025-01-01T00:00:00Z" },
];

function mountPage() {
  return mount(DeploymentSlotPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: { template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        EmptyState: { template: '<div class="empty-state">{{ title }}<slot /></div>', props: ["icon", "title", "description"] },
        DataTable: { template: '<div class="datatable"><slot /></div>' },
        Column: { template: '<div class="column"><slot /></div>' },
        Dialog: { template: '<div class="dialog" v-if="$attrs.visible"><slot /><slot name="footer" /></div>', inheritAttrs: false },
        InputText: { template: '<input />', inheritAttrs: false },
        InputSwitch: { template: '<input type="checkbox" />', inheritAttrs: false },
        Select: { template: '<select />', inheritAttrs: false },
        ConfirmDialog: {
          template: '<div class="confirm-dialog" v-if="$attrs.visible"><button class="confirm-btn" @click="$emit(\'confirm\')">Confirm</button></div>',
          inheritAttrs: false,
          emits: ["confirm"],
        },
      },
    },
  });
}

describe("DeploymentSlotPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    apiMock.mockResolvedValue({ results: [] }); // for fetchModels
  });

  it("renders loading state initially", async () => {
    mockGetSlots.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders slots on success", async () => {
    mockGetSlots.mockResolvedValue({ results: sampleSlots });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".datatable").exists()).toBe(true);
    expect(wrapper.text()).toContain("Total weight:");
  });

  it("renders empty state when no slots", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No deployment slots");
  });

  it("renders error with retry on failure", async () => {
    mockGetSlots.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Network error");
  });

  it("ignores AbortError", async () => {
    mockGetSlots.mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("retries on retry button click", async () => {
    mockGetSlots
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValueOnce({ results: sampleSlots });
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(mockGetSlots).toHaveBeenCalledTimes(2);
  });

  it("renders page title", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find("h2").text()).toContain("Deployment Slots");
  });

  it("has New Slot button", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("New Slot");
  });

  it("does not show empty state when error", async () => {
    mockGetSlots.mockRejectedValueOnce(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(false);
  });

  it("fetches models on mount", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    mountPage();
    await flushPromises();
    expect(apiMock).toHaveBeenCalled();
  });

  it("hides skeleton after load", async () => {
    mockGetSlots.mockResolvedValue({ results: sampleSlots });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("hides skeleton after error", async () => {
    mockGetSlots.mockRejectedValueOnce(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("openCreateDialog resets form and opens dialog", async () => {
    mockGetSlots.mockResolvedValue({ results: sampleSlots });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.openCreateDialog();
    expect(vm.showCreateDialog).toBe(true);
    expect(vm.createForm.name).toBe("");
    expect(vm.createForm.trained_model).toBeNull();
    expect(vm.createForm.weight).toBe(50);
  });

  it("handleCreate creates slot and pushes to list", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    mockCreateSlot.mockResolvedValue({ id: 3, name: "New", trained_model: 10, weight: 50, is_active: true, updated_at: "2025-01-01T00:00:00Z" });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.name = "New";
    vm.createForm.trained_model = 10;
    vm.createForm.weight = 50;
    await vm.handleCreate();
    await flushPromises();

    expect(mockCreateSlot).toHaveBeenCalled();
    expect(notifySuccess).toHaveBeenCalledWith("Deployment slot created.");
    expect(vm.showCreateDialog).toBe(false);
    expect(vm.slots.length).toBe(1);
  });

  it("handleCreate early returns if no model selected", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.trained_model = null;
    await vm.handleCreate();
    expect(mockCreateSlot).not.toHaveBeenCalled();
  });

  it("handleCreate shows error on failure", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    mockCreateSlot.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.name = "New";
    vm.createForm.trained_model = 10;
    await vm.handleCreate();
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to create deployment slot.");
  });

  it("handleWeightChange updates slot weight", async () => {
    mockGetSlots.mockResolvedValue({ results: [...sampleSlots] });
    mockUpdateSlot.mockResolvedValue({ ...sampleSlots[0], weight: 80 });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.handleWeightChange(sampleSlots[0], 80);
    await flushPromises();

    expect(mockUpdateSlot).toHaveBeenCalledWith(1, { weight: 80 });
  });

  it("handleWeightChange shows error on failure", async () => {
    mockGetSlots.mockResolvedValue({ results: [...sampleSlots] });
    mockUpdateSlot.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.handleWeightChange(sampleSlots[0], 80);
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to update weight.");
  });

  it("handleToggleActive toggles slot active status", async () => {
    mockGetSlots.mockResolvedValue({ results: [...sampleSlots] });
    mockUpdateSlot.mockResolvedValue({ ...sampleSlots[0], is_active: false });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.handleToggleActive(sampleSlots[0], false);
    await flushPromises();

    expect(mockUpdateSlot).toHaveBeenCalledWith(1, { is_active: false });
  });

  it("handleToggleActive shows error on failure", async () => {
    mockGetSlots.mockResolvedValue({ results: [...sampleSlots] });
    mockUpdateSlot.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    await vm.handleToggleActive(sampleSlots[0], false);
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to update slot status.");
  });

  it("confirmDelete sets target and shows dialog", async () => {
    mockGetSlots.mockResolvedValue({ results: sampleSlots });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.confirmDelete(sampleSlots[0]);
    expect(vm.deleteTarget).toEqual(sampleSlots[0]);
    expect(vm.showDeleteConfirm).toBe(true);
  });

  it("executeDelete deletes slot and removes from list", async () => {
    mockGetSlots.mockResolvedValue({ results: [...sampleSlots] });
    mockDeleteSlot.mockResolvedValue({});
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.deleteTarget = sampleSlots[0];
    await vm.executeDelete();
    await flushPromises();

    expect(mockDeleteSlot).toHaveBeenCalledWith(1);
    expect(notifySuccess).toHaveBeenCalledWith("Deployment slot deleted.");
    expect(vm.slots.length).toBe(1);
    expect(vm.deleteTarget).toBeNull();
  });

  it("executeDelete shows error on failure", async () => {
    mockGetSlots.mockResolvedValue({ results: [...sampleSlots] });
    mockDeleteSlot.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.deleteTarget = sampleSlots[0];
    await vm.executeDelete();
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to delete deployment slot.");
  });

  it("executeDelete early returns if no target", async () => {
    mockGetSlots.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.deleteTarget = null;
    await vm.executeDelete();
    expect(mockDeleteSlot).not.toHaveBeenCalled();
  });

  it("computes activeSlots and totalWeight correctly", async () => {
    mockGetSlots.mockResolvedValue({ results: sampleSlots });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    expect(vm.activeSlots.length).toBe(2);
    expect(vm.totalWeight).toBe(100);
  });
});
