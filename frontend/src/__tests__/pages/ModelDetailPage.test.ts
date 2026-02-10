import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ModelDetailPage from "@/pages/ModelDetailPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const pushMock = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
  useRoute: () => ({ params: { projectId: "1", modelId: "10" } }),
}));

const apiMock = vi.fn();

vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

const notifyMock = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => notifyMock,
}));

const mockModel = {
  id: 10,
  configuration: 1,
  data_loc: 1,
  irspack_version: "0.4.0",
  file: "/data/model.pkl",
  ins_datetime: "2025-01-01T00:00:00Z",
  basename: "model.pkl",
  filesize: 1024,
};

function mountPage() {
  return mount(ModelDetailPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        DataTable: { template: '<table class="data-table"><slot /></table>' },
        Column: true,
        InputText: {
          template: '<input class="input-text" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
          props: ["modelValue", "placeholder"],
          emits: ["update:modelValue"],
        },
        InputNumber: {
          template: '<input class="input-number" type="number" :value="modelValue" @input="$emit(\'update:modelValue\', Number($event.target.value))" />',
          props: ["modelValue", "min", "max", "placeholder"],
          emits: ["update:modelValue"],
        },
        Button: {
          template: '<button :disabled="$attrs.disabled" :class="{ loading: $attrs.loading }" @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
      },
    },
  });
}

describe("ModelDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
    apiMock.mockResolvedValue(mockModel);
  });

  it("renders model heading with model ID", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find("h2").exists()).toBe(true);
    expect(wrapper.text()).toContain("Model");
    expect(wrapper.text()).toContain("#10");
  });

  it("fetches model data on mount", async () => {
    mountPage();
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      expect.stringContaining("trained_model"),
    );
  });

  it("displays model metadata after load", async () => {
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("model.pkl"); // basename
    expect(wrapper.text()).toContain("0.4.0"); // irspack_version
  });

  it("renders recommendation preview section", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toMatch(/Recommend|Preview/i);
  });

  it("renders user ID input field", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".input-text").exists()).toBe(true);
  });

  it("renders top-K input field", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find(".input-number").exists()).toBe(true);
  });

  it("renders get recommendations button", async () => {
    const wrapper = mountPage();
    await flushPromises();
    const buttons = wrapper.findAll("button");
    const recBtn = buttons.find(b => b.text().match(/Recommend|Get/i));
    expect(recBtn).toBeDefined();
  });

  it("does not render model details before data loads", () => {
    apiMock.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    // model.value is null, so the detail section should not render
    expect(wrapper.find(".data-table").exists()).toBe(false);
  });

  it("does not fetch recommendations when user ID is empty", async () => {
    const wrapper = mountPage();
    await flushPromises();

    // Click the recommendation button without entering a user ID
    const buttons = wrapper.findAll("button");
    const recBtn = buttons.find(b => b.text().match(/Recommend|Get/i));
    await recBtn!.trigger("click");
    await flushPromises();

    // Only the initial model fetch should have been called
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it("fetches recommendations when user ID is provided", async () => {
    apiMock
      .mockResolvedValueOnce(mockModel) // initial model fetch
      .mockResolvedValueOnce([
        { item_id: "item_1", score: 0.95 },
        { item_id: "item_2", score: 0.87 },
      ]); // recommendations

    const wrapper = mountPage();
    await flushPromises();

    // Enter user ID
    const userInput = wrapper.find(".input-text");
    await userInput.setValue("user_42");
    await nextTick();

    // Click get recommendations
    const buttons = wrapper.findAll("button");
    const recBtn = buttons.find(b => b.text().match(/Recommend|Get/i));
    await recBtn!.trigger("click");
    await flushPromises();

    expect(apiMock).toHaveBeenCalledTimes(2);
    expect(apiMock).toHaveBeenLastCalledWith(
      expect.stringContaining("recommendation"),
      expect.objectContaining({
        params: expect.objectContaining({ user_id: "user_42" }),
      }),
    );
  });

  it("displays recommendation results in a table", async () => {
    apiMock
      .mockResolvedValueOnce(mockModel)
      .mockResolvedValueOnce([
        { item_id: "item_1", score: 0.95 },
        { item_id: "item_2", score: 0.87 },
      ]);

    const wrapper = mountPage();
    await flushPromises();

    const userInput = wrapper.find(".input-text");
    await userInput.setValue("user_42");
    await nextTick();

    const buttons = wrapper.findAll("button");
    const recBtn = buttons.find(b => b.text().match(/Recommend|Get/i));
    await recBtn!.trigger("click");
    await flushPromises();

    expect(wrapper.find(".data-table").exists()).toBe(true);
  });

  it("shows no-recommendations message when result is empty", async () => {
    apiMock
      .mockResolvedValueOnce(mockModel)
      .mockResolvedValueOnce([]); // empty recommendations

    const wrapper = mountPage();
    await flushPromises();

    const userInput = wrapper.find(".input-text");
    await userInput.setValue("user_99");
    await nextTick();

    const buttons = wrapper.findAll("button");
    const recBtn = buttons.find(b => b.text().match(/Recommend|Get/i));
    await recBtn!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toMatch(/no recommendation/i);
  });

  it("shows error notification when recommendation fetch fails", async () => {
    const error = new Error("Server error");
    (error as any).data = { detail: "Model not ready" };
    apiMock
      .mockResolvedValueOnce(mockModel)
      .mockRejectedValueOnce(error);

    const wrapper = mountPage();
    await flushPromises();

    const userInput = wrapper.find(".input-text");
    await userInput.setValue("user_1");
    await nextTick();

    const buttons = wrapper.findAll("button");
    const recBtn = buttons.find(b => b.text().match(/Recommend|Get/i));
    await recBtn!.trigger("click");
    await flushPromises();

    expect(notifyMock.error).toHaveBeenCalledWith("Model not ready");
  });

  it("uses fallback error message when detail is missing", async () => {
    apiMock
      .mockResolvedValueOnce(mockModel)
      .mockRejectedValueOnce(new Error("Network error"));

    const wrapper = mountPage();
    await flushPromises();

    const userInput = wrapper.find(".input-text");
    await userInput.setValue("user_1");
    await nextTick();

    const buttons = wrapper.findAll("button");
    const recBtn = buttons.find(b => b.text().match(/Recommend|Get/i));
    await recBtn!.trigger("click");
    await flushPromises();

    // Should use fallback i18n message
    expect(notifyMock.error).toHaveBeenCalled();
  });
});
