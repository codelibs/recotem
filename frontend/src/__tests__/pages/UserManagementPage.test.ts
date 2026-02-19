import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import UserManagementPage from "@/pages/UserManagementPage.vue";
import type { ManagedUser } from "@/types";

const mockGetUsers = vi.fn();
const mockCreateUser = vi.fn();
const mockDeactivateUser = vi.fn();
const mockActivateUser = vi.fn();
const mockResetUserPassword = vi.fn();

vi.mock("@/api/users", () => ({
  getUsers: (...args: unknown[]) => mockGetUsers(...args),
  createUser: (...args: unknown[]) => mockCreateUser(...args),
  deactivateUser: (...args: unknown[]) => mockDeactivateUser(...args),
  activateUser: (...args: unknown[]) => mockActivateUser(...args),
  resetUserPassword: (...args: unknown[]) => mockResetUserPassword(...args),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => ({ signal: undefined }),
}));

const mockNotifySuccess = vi.fn();
const mockNotifyError = vi.fn();
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({ success: mockNotifySuccess, error: mockNotifyError }),
}));

vi.mock("@/api/client", () => ({
  classifyApiError: (err: any) => ({
    kind: "unknown",
    status: null,
    message: err?.message ?? "Unknown error",
    fieldErrors: err?.data ?? undefined,
  }),
}));

const activeUser: ManagedUser = {
  id: 1,
  username: "alice",
  email: "alice@example.com",
  is_staff: false,
  is_active: true,
  date_joined: "2025-01-01T00:00:00Z",
  last_login: "2025-06-01T00:00:00Z",
};

const inactiveUser: ManagedUser = {
  id: 2,
  username: "bob",
  email: "bob@example.com",
  is_staff: false,
  is_active: false,
  date_joined: "2025-02-01T00:00:00Z",
  last_login: null,
};

function mountPage() {
  return mount(UserManagementPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        DataTable: {
          template: '<div class="data-table"><slot name="empty" /><div v-for="item in value" :key="item.id" class="row"><slot name="body-row" :data="item" /></div></div>',
          props: ["value", "stripedRows", "paginator", "rows"],
        },
        Column: { template: '<span />', props: ["field", "header", "sortable", "style"] },
        Button: {
          template: '<button @click="$attrs.onClick?.()">{{ $attrs.label ?? $attrs["aria-label"] }}</button>',
          inheritAttrs: false,
        },
        Tag: { template: '<span class="tag">{{ value }}</span>', props: ["value", "severity"] },
        Message: { template: '<div class="message" role="alert"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        Dialog: {
          template: '<div v-if="visible" class="dialog"><slot /><slot name="footer" /></div>',
          props: ["visible", "header", "modal"],
        },
        InputText: {
          template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
          props: ["modelValue"],
          emits: ["update:modelValue"],
        },
        Password: {
          template: '<input type="password" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
          props: ["modelValue"],
          emits: ["update:modelValue"],
          inheritAttrs: false,
        },
        ToggleSwitch: {
          template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
          props: ["modelValue"],
          emits: ["update:modelValue"],
        },
        ConfirmDialog: {
          template: '<div class="confirm-dialog" />',
          props: ["visible", "header", "message", "confirmLabel", "cancelLabel", "danger"],
          emits: ["update:visible", "confirm", "cancel"],
        },
      },
    },
  });
}

describe("UserManagementPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("shows loading skeletons during initial fetch", async () => {
    mockGetUsers.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("shows error message when fetch fails", async () => {
    mockGetUsers.mockRejectedValueOnce(new Error("Server error"));
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Server error");
  });

  it("shows data table after successful fetch", async () => {
    mockGetUsers.mockResolvedValueOnce([activeUser]);
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".data-table").exists()).toBe(true);
    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  it("fetches users on mount", async () => {
    mockGetUsers.mockResolvedValueOnce([activeUser]);
    mountPage();
    await flushPromises();
    expect(mockGetUsers).toHaveBeenCalledTimes(1);
  });

  it("does not show error when AbortError occurs", async () => {
    const abortError = new DOMException("Aborted", "AbortError");
    Object.defineProperty(abortError, "name", { value: "AbortError" });
    mockGetUsers.mockRejectedValueOnce(abortError);
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(false);
  });

  it("retries fetch when retry button is clicked", async () => {
    mockGetUsers
      .mockRejectedValueOnce(new Error("Fail"))
      .mockResolvedValueOnce([activeUser]);
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("retry"));
    expect(retryBtn).toBeDefined();
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(mockGetUsers).toHaveBeenCalledTimes(2);
  });

  it("opens create dialog when New User button clicked", async () => {
    mockGetUsers.mockResolvedValueOnce([]);
    const wrapper = mountPage();
    await flushPromises();

    const newUserBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("new user"));
    expect(newUserBtn).toBeDefined();
    await newUserBtn!.trigger("click");
    expect(wrapper.find(".dialog").exists()).toBe(true);
  });

  it("calls createUser with form data", async () => {
    mockGetUsers.mockResolvedValue([]);
    mockCreateUser.mockResolvedValueOnce({ ...activeUser, id: 99, username: "newuser" });
    const wrapper = mountPage();
    await flushPromises();

    // Open dialog
    const newUserBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("new user"));
    await newUserBtn!.trigger("click");

    // Fill in username and password
    const inputs = wrapper.findAll("input:not([type='checkbox']):not([type='password'])");
    await inputs[0].setValue("newuser");

    const passwordInput = wrapper.find("input[type='password']");
    await passwordInput.setValue("secret");

    // Click Create button
    const createBtn = wrapper.findAll("button").find(b => b.text().toLowerCase() === "create");
    expect(createBtn).toBeDefined();
    await createBtn!.trigger("click");
    await flushPromises();

    expect(mockCreateUser).toHaveBeenCalled();
  });

  it("shows create error on failure", async () => {
    mockGetUsers.mockResolvedValue([]);
    mockCreateUser.mockRejectedValueOnce(new Error("Username taken"));
    const wrapper = mountPage();
    await flushPromises();

    const newUserBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("new user"));
    await newUserBtn!.trigger("click");

    const inputs = wrapper.findAll("input:not([type='checkbox']):not([type='password'])");
    await inputs[0].setValue("alice");
    const passwordInput = wrapper.find("input[type='password']");
    await passwordInput.setValue("pass");

    const createBtn = wrapper.findAll("button").find(b => b.text().toLowerCase() === "create");
    await createBtn!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Username taken");
  });

  it("calls activateUser and refreshes list", async () => {
    mockGetUsers
      .mockResolvedValueOnce([inactiveUser])
      .mockResolvedValueOnce([{ ...inactiveUser, is_active: true }]);
    mockActivateUser.mockResolvedValueOnce({ ...inactiveUser, is_active: true });

    const wrapper = mountPage();
    await flushPromises();

    const activateBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("activate"));
    if (activateBtn) {
      await activateBtn.trigger("click");
      await flushPromises();
      expect(mockActivateUser).toHaveBeenCalledWith(inactiveUser.id);
    }
  });

  it("shows success notification after activation", async () => {
    mockGetUsers
      .mockResolvedValueOnce([inactiveUser])
      .mockResolvedValueOnce([inactiveUser]);
    mockActivateUser.mockResolvedValueOnce(inactiveUser);
    const wrapper = mountPage();
    await flushPromises();

    const activateBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("activate"));
    if (activateBtn) {
      await activateBtn.trigger("click");
      await flushPromises();
      expect(mockNotifySuccess).toHaveBeenCalled();
    }
  });

  it("shows error notification when activation fails", async () => {
    mockGetUsers.mockResolvedValueOnce([inactiveUser]);
    mockActivateUser.mockRejectedValueOnce(new Error("Fail"));
    const wrapper = mountPage();
    await flushPromises();

    const activateBtn = wrapper.findAll("button").find(b => b.text().toLowerCase().includes("activate"));
    if (activateBtn) {
      await activateBtn.trigger("click");
      await flushPromises();
      expect(mockNotifyError).toHaveBeenCalled();
    }
  });
});
