import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import PasswordChangePage from "@/pages/PasswordChangePage.vue";

const mockChangeOwnPassword = vi.fn();
vi.mock("@/api/users", () => ({
  changeOwnPassword: (...args: unknown[]) => mockChangeOwnPassword(...args),
}));

vi.mock("@/api/client", () => ({
  classifyApiError: (err: any) => ({
    kind: "unknown",
    status: null,
    message: err?.data?.old_password?.[0] ?? err?.message ?? "Unknown error",
    fieldErrors: err?.data ?? undefined,
  }),
}));

function mountPage() {
  return mount(PasswordChangePage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Password: {
          template: '<input :id="$attrs.id" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" type="password" />',
          props: ["modelValue"],
          emits: ["update:modelValue"],
          inheritAttrs: false,
        },
        Button: {
          template: '<button :disabled="disabled || loading" @click="$attrs.onClick?.()">{{ label }}</button>',
          props: ["label", "disabled", "loading"],
          inheritAttrs: false,
        },
        Message: {
          template: '<div :class="severity"><slot /></div>',
          props: ["severity"],
        },
      },
    },
  });
}

describe("PasswordChangePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders the change password heading", () => {
    const wrapper = mountPage();
    expect(wrapper.find("h2").exists()).toBe(true);
  });

  it("renders three password inputs", () => {
    const wrapper = mountPage();
    expect(wrapper.findAll("input[type='password']").length).toBe(3);
  });

  it("submit button is disabled when fields are empty", () => {
    const wrapper = mountPage();
    const btn = wrapper.find("button");
    expect(btn.attributes("disabled")).toBeDefined();
  });

  it("submit button is disabled when new passwords do not match", async () => {
    const wrapper = mountPage();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("oldpass");
    await inputs[1].setValue("newpass1");
    await inputs[2].setValue("newpass2_different");
    const btn = wrapper.find("button");
    expect(btn.attributes("disabled")).toBeDefined();
  });

  it("submit button is enabled when all fields are filled and passwords match", async () => {
    const wrapper = mountPage();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("oldpass");
    await inputs[1].setValue("newpass");
    await inputs[2].setValue("newpass");
    const btn = wrapper.find("button");
    expect(btn.attributes("disabled")).toBeUndefined();
  });

  it("calls changeOwnPassword with correct arguments on submit", async () => {
    mockChangeOwnPassword.mockResolvedValueOnce(undefined);
    const wrapper = mountPage();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("oldpass");
    await inputs[1].setValue("newpass");
    await inputs[2].setValue("newpass");
    await wrapper.find("button").trigger("click");
    await flushPromises();
    expect(mockChangeOwnPassword).toHaveBeenCalledWith("oldpass", "newpass");
  });

  it("shows success message after successful password change", async () => {
    mockChangeOwnPassword.mockResolvedValueOnce(undefined);
    const wrapper = mountPage();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("old");
    await inputs[1].setValue("new");
    await inputs[2].setValue("new");
    await wrapper.find("button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".success").exists()).toBe(true);
  });

  it("clears form after successful password change", async () => {
    mockChangeOwnPassword.mockResolvedValueOnce(undefined);
    const wrapper = mountPage();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("old");
    await inputs[1].setValue("new");
    await inputs[2].setValue("new");
    await wrapper.find("button").trigger("click");
    await flushPromises();
    for (const input of wrapper.findAll("input")) {
      expect((input.element as HTMLInputElement).value).toBe("");
    }
  });

  it("shows error message on API failure", async () => {
    mockChangeOwnPassword.mockRejectedValueOnce(new Error("Wrong password"));
    const wrapper = mountPage();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("wrong");
    await inputs[1].setValue("new");
    await inputs[2].setValue("new");
    await wrapper.find("button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".error").exists()).toBe(true);
  });
});
