import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import ProjectCreateForm from "@/components/project/ProjectCreateForm.vue";

// Mock project store
const mockCreateProject = vi.fn();
vi.mock("@/stores/project", () => ({
  useProjectStore: () => ({ createProject: mockCreateProject }),
}));

// Mock notification composable
const mockNotifySuccess = vi.fn();
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => ({ success: mockNotifySuccess, error: vi.fn() }),
}));

function mountForm() {
  return mount(ProjectCreateForm, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        FormField: {
          template: '<div><slot :id="name" :has-error="!!error" /><span v-if="error" class="error">{{ error }}</span></div>',
          props: ["label", "name", "error", "required", "hint"],
        },
        InputText: {
          template: '<input :id="id" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @blur="$attrs.onBlur?.($event)" />',
          props: ["modelValue", "id", "invalid"],
          emits: ["update:modelValue"],
          inheritAttrs: false,
        },
        Button: {
          template: '<button :type="type ?? \'button\'" :disabled="loading" @click="$attrs.onClick?.()">{{ label }}<slot /></button>',
          props: ["label", "type", "loading", "severity"],
          inheritAttrs: false,
        },
        Message: {
          template: '<div class="error-msg" role="alert"><slot /></div>',
        },
      },
    },
  });
}

describe("ProjectCreateForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders all four input fields", () => {
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    expect(inputs.length).toBe(4);
  });

  it("emits cancel when Cancel button clicked", async () => {
    const wrapper = mountForm();
    const cancelBtn = wrapper.findAll("button").find(b => b.text() === "Cancel");
    expect(cancelBtn).toBeDefined();
    await cancelBtn!.trigger("click");
    expect(wrapper.emitted("cancel")).toBeTruthy();
  });

  it("shows validation error when submitting with empty name", async () => {
    const wrapper = mountForm();
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("required");
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("shows validation error for empty user_column", async () => {
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("My Project");
    // Leave user_column (inputs[1]) empty
    await inputs[2].setValue("item_id");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("required");
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("calls createProject with form data on valid submit", async () => {
    mockCreateProject.mockResolvedValueOnce({ id: 1, name: "Test" });
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("My Project");
    await inputs[1].setValue("user_id");
    await inputs[2].setValue("item_id");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(mockCreateProject).toHaveBeenCalledWith({
      name: "My Project",
      user_column: "user_id",
      item_column: "item_id",
      time_column: null,
    });
  });

  it("includes time_column when provided", async () => {
    mockCreateProject.mockResolvedValueOnce({ id: 1 });
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("Project");
    await inputs[1].setValue("user_id");
    await inputs[2].setValue("item_id");
    await inputs[3].setValue("timestamp");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(mockCreateProject).toHaveBeenCalledWith(
      expect.objectContaining({ time_column: "timestamp" }),
    );
  });

  it("emits created and shows success notification after successful submit", async () => {
    mockCreateProject.mockResolvedValueOnce({ id: 1 });
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("Project");
    await inputs[1].setValue("user_id");
    await inputs[2].setValue("item_id");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.emitted("created")).toBeTruthy();
    expect(mockNotifySuccess).toHaveBeenCalled();
  });

  it("shows server field error on 400 response", async () => {
    const err = { data: { name: ["This name already exists."] } };
    mockCreateProject.mockRejectedValueOnce(err);
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("Duplicate");
    await inputs[1].setValue("user_id");
    await inputs[2].setValue("item_id");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("This name already exists.");
  });

  it("shows generic error message on unknown API error", async () => {
    mockCreateProject.mockRejectedValueOnce(new Error("Server error"));
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("Project");
    await inputs[1].setValue("user_id");
    await inputs[2].setValue("item_id");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.find(".error-msg").exists()).toBe(true);
    expect(wrapper.text()).toContain("Failed to create project");
  });

  it("shows detail error from server response", async () => {
    const err = { data: { detail: "You do not have permission." } };
    mockCreateProject.mockRejectedValueOnce(err);
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("Project");
    await inputs[1].setValue("user_id");
    await inputs[2].setValue("item_id");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("You do not have permission.");
  });

  it("shows non_field_errors from server response", async () => {
    const err = { data: { non_field_errors: ["Duplicate project name."] } };
    mockCreateProject.mockRejectedValueOnce(err);
    const wrapper = mountForm();
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("Project");
    await inputs[1].setValue("user_id");
    await inputs[2].setValue("item_id");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("Duplicate project name.");
  });
});
