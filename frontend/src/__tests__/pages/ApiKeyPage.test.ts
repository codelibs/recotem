import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ApiKeyPage from "@/pages/ApiKeyPage.vue";
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

vi.mock("@/api/client", () => ({
  classifyApiError: (err: any) => ({ kind: "unknown", message: err?.message ?? "Unknown error" }),
  unwrapResults: (res: any) => (Array.isArray(res) ? res : res.results),
}));

const mockGetKeys = vi.fn();
const mockCreateKey = vi.fn();
const mockRevokeKey = vi.fn();
const mockDeleteKey = vi.fn();

vi.mock("@/api/production", () => ({
  getApiKeys: (...args: unknown[]) => mockGetKeys(...args),
  createApiKey: (...args: unknown[]) => mockCreateKey(...args),
  revokeApiKey: (...args: unknown[]) => mockRevokeKey(...args),
  deleteApiKey: (...args: unknown[]) => mockDeleteKey(...args),
}));

const sampleKeys = [
  { id: 1, name: "Production", key_prefix: "reco_abc", scopes: ["predict", "batch"], is_active: true, last_used_at: "2025-01-01T00:00:00Z", ins_datetime: "2025-01-01T00:00:00Z" },
  { id: 2, name: "Revoked Key", key_prefix: "reco_xyz", scopes: ["admin"], is_active: false, last_used_at: null, ins_datetime: "2025-01-01T00:00:00Z" },
];

function mountPage() {
  return mount(ApiKeyPage, {
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
        MultiSelect: { template: '<select />', inheritAttrs: false },
        DatePicker: { template: '<input />', inheritAttrs: false },
        ConfirmDialog: {
          template: '<div class="confirm-dialog" v-if="$attrs.visible"><button class="confirm-btn" @click="$emit(\'confirm\')">Confirm</button></div>',
          inheritAttrs: false,
          emits: ["confirm"],
        },
      },
    },
  });
}

describe("ApiKeyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders loading state initially", async () => {
    mockGetKeys.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renders keys on success", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".datatable").exists()).toBe(true);
  });

  it("renders empty state when no keys", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No API keys");
  });

  it("renders error with retry on failure", async () => {
    mockGetKeys.mockRejectedValueOnce(new Error("Network error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Network error");
  });

  it("ignores AbortError", async () => {
    mockGetKeys.mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("retries on retry button click", async () => {
    mockGetKeys
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValueOnce({ results: sampleKeys });
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(mockGetKeys).toHaveBeenCalledTimes(2);
  });

  it("renders page title", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find("h2").text()).toContain("API Keys");
  });

  it("has New API Key button", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("New API Key");
  });

  it("does not show empty state when error", async () => {
    mockGetKeys.mockRejectedValueOnce(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".empty-state").exists()).toBe(false);
  });

  it("hides skeleton after load", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("hides skeleton after error", async () => {
    mockGetKeys.mockRejectedValueOnce(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("handleCreate creates key and reveals it", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    mockCreateKey.mockResolvedValue({ id: 3, key: "reco_secret123", name: "New" });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.name = "New Key";
    vm.createForm.scopes = ["predict"];
    vm.showCreateDialog = true;
    await vm.handleCreate();
    await flushPromises();

    expect(mockCreateKey).toHaveBeenCalled();
    expect(vm.revealedKey).toBe("reco_secret123");
    expect(vm.showKeyReveal).toBe(true);
    expect(vm.showCreateDialog).toBe(false);
  });

  it("handleCreate with expires_at includes it in payload", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    mockCreateKey.mockResolvedValue({ id: 3, key: "reco_abc", name: "New" });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.name = "Key";
    vm.createForm.scopes = ["admin"];
    vm.createForm.expires_at = new Date("2025-12-31T00:00:00Z");
    await vm.handleCreate();
    await flushPromises();

    expect(mockCreateKey).toHaveBeenCalledWith(expect.objectContaining({
      expires_at: expect.any(String),
    }));
  });

  it("handleCreate shows error on failure", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    mockCreateKey.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.createForm.name = "Key";
    vm.createForm.scopes = ["predict"];
    await vm.handleCreate();
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to create API key.");
  });

  it("copyKey copies to clipboard", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: writeTextMock } });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.revealedKey = "reco_secret123";
    vm.copyKey();

    expect(writeTextMock).toHaveBeenCalledWith("reco_secret123");
    expect(notifySuccess).toHaveBeenCalledWith("Key copied to clipboard.");
  });

  it("confirmRevoke sets target and shows dialog", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.confirmRevoke(sampleKeys[0]);
    expect(vm.revokeTarget).toEqual(sampleKeys[0]);
    expect(vm.showRevokeConfirm).toBe(true);
  });

  it("executeRevoke revokes key and refreshes", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    mockRevokeKey.mockResolvedValue({});
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.revokeTarget = sampleKeys[0];
    await vm.executeRevoke();
    await flushPromises();

    expect(mockRevokeKey).toHaveBeenCalledWith(1);
    expect(notifySuccess).toHaveBeenCalledWith("API key revoked.");
    expect(vm.revokeTarget).toBeNull();
  });

  it("executeRevoke shows error on failure", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    mockRevokeKey.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.revokeTarget = sampleKeys[0];
    await vm.executeRevoke();
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to revoke API key.");
  });

  it("executeRevoke early returns if no target", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.revokeTarget = null;
    await vm.executeRevoke();
    expect(mockRevokeKey).not.toHaveBeenCalled();
  });

  it("confirmDelete sets target and shows dialog", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.confirmDelete(sampleKeys[1]);
    expect(vm.deleteTarget).toEqual(sampleKeys[1]);
    expect(vm.showDeleteConfirm).toBe(true);
  });

  it("executeDelete deletes key and removes from list", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    mockDeleteKey.mockResolvedValue({});
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.deleteTarget = sampleKeys[1];
    await vm.executeDelete();
    await flushPromises();

    expect(mockDeleteKey).toHaveBeenCalledWith(2);
    expect(notifySuccess).toHaveBeenCalledWith("API key deleted.");
    expect(vm.keys.length).toBe(1);
    expect(vm.deleteTarget).toBeNull();
  });

  it("executeDelete shows error on failure", async () => {
    mockGetKeys.mockResolvedValue({ results: sampleKeys });
    mockDeleteKey.mockRejectedValue(new Error("fail"));
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.deleteTarget = sampleKeys[1];
    await vm.executeDelete();
    await flushPromises();

    expect(notifyError).toHaveBeenCalledWith("Failed to delete API key.");
  });

  it("executeDelete early returns if no target", async () => {
    mockGetKeys.mockResolvedValue({ results: [] });
    const wrapper = mountPage();
    await flushPromises();

    const vm = wrapper.vm as any;
    vm.deleteTarget = null;
    await vm.executeDelete();
    expect(mockDeleteKey).not.toHaveBeenCalled();
  });
});
