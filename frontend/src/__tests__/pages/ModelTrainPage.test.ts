import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ModelTrainPage from "@/pages/ModelTrainPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

vi.mock("@/composables/useAbortOnUnmount", () => ({
  useAbortOnUnmount: () => new AbortController(),
}));

const notifyMock = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => notifyMock,
}));

const apiMock = vi.fn();
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  classifyApiError: (err: any) => ({
    kind: "unknown",
    status: undefined,
    message: err?.message ?? "Unknown error",
    fieldErrors: undefined,
  }),
  unwrapResults: (res: any) => (Array.isArray(res) ? res : res?.results ?? []),
}));

function mountPage() {
  return mount(ModelTrainPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: {
          template:
            '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        DataTable: {
          template: '<div class="data-table"><slot /></div>',
          props: ["value", "loading"],
        },
        Column: { template: "<div />" },
        Tag: {
          template: '<span class="tag">{{ value }}</span>',
          props: ["severity", "value"],
        },
        Select: {
          template: '<select class="select-stub" @change="$emit(\'update:modelValue\', Number($event.target.value))"><option v-for="o in (options || [])" :key="o[optionValue]" :value="o[optionValue]">{{ o[optionLabel] }}</option></select>',
          props: ["modelValue", "options", "optionLabel", "optionValue", "placeholder"],
          emits: ["update:modelValue"],
        },
        Dialog: {
          template:
            '<div class="dialog" v-if="visible"><slot /></div>',
          props: ["visible", "header"],
        },
        EmptyState: {
          template:
            '<div class="empty-state">{{ title }}<slot /></div>',
          props: ["icon", "title", "description"],
        },
        ConfirmDialog: { template: "<div />" },
        RouterLink: { template: "<a><slot /></a>" },
      },
    },
  });
}

describe("ModelTrainPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders page title 'Train Model'", async () => {
    apiMock
      .mockResolvedValueOnce({ results: [{ id: 1, basename: "data.csv" }] })
      .mockResolvedValueOnce({
        results: [
          {
            id: 1,
            name: "config1",
            recommender_class_name: "IALSRecommender",
          },
        ],
      });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Train Model");
  });

  it("renders form with select components on data load", async () => {
    apiMock
      .mockResolvedValueOnce({ results: [{ id: 1, basename: "data.csv" }] })
      .mockResolvedValueOnce({
        results: [
          {
            id: 1,
            name: "config1",
            recommender_class_name: "IALSRecommender",
          },
        ],
      });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.findAll(".select-stub").length).toBe(2);
  });

  it("renders back button and navigates on click", async () => {
    apiMock
      .mockResolvedValueOnce({ results: [{ id: 1, basename: "data.csv" }] })
      .mockResolvedValueOnce({
        results: [
          {
            id: 1,
            name: "config1",
            recommender_class_name: "IALSRecommender",
          },
        ],
      });
    // Mount with real Button to cover the template onClick handler
    const wrapper = mount(ModelTrainPage, {
      global: {
        plugins: [PrimeVue, createPinia(), i18n],
        stubs: {
          Message: { template: '<div class="message"><slot /></div>' },
          Select: { template: "<select />", props: ["modelValue", "options", "optionLabel", "optionValue", "placeholder"] },
        },
      },
    });
    await flushPromises();
    // Find and click the back arrow button
    const backBtn = wrapper.find("button");
    expect(backBtn.exists()).toBe(true);
    await backBtn.trigger("click");
    expect(mockPush).toHaveBeenCalledWith("/projects/1/models");
  });

  it("renders submit button with Start Training label", async () => {
    apiMock
      .mockResolvedValueOnce({ results: [{ id: 1, basename: "data.csv" }] })
      .mockResolvedValueOnce({
        results: [
          {
            id: 1,
            name: "config1",
            recommender_class_name: "IALSRecommender",
          },
        ],
      });
    const wrapper = mountPage();
    await flushPromises();
    const submitBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Start Training"));
    expect(submitBtn).toBeDefined();
  });

  it("renders loading state when API has not resolved", async () => {
    apiMock.mockReturnValue(new Promise(() => {}));
    const wrapper = mountPage();
    await nextTick();
    // Page should still render the title
    expect(wrapper.text()).toContain("Train Model");
  });

  it("renders labels for data and configuration selects", async () => {
    apiMock
      .mockResolvedValueOnce({
        results: [
          { id: 1, basename: "data.csv" },
          { id: 2, basename: "ratings.csv" },
        ],
      })
      .mockResolvedValueOnce({
        results: [
          {
            id: 1,
            name: "config1",
            recommender_class_name: "IALSRecommender",
          },
          {
            id: 2,
            name: "",
            recommender_class_name: "P3alphaRecommender",
          },
        ],
      });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Training Data");
    expect(wrapper.text()).toContain("Configuration");
  });

  describe("submitTraining", () => {
    it("submits training when form is filled and button clicked", async () => {
      // onMounted fetches data + configs
      apiMock
        .mockResolvedValueOnce({
          results: [{ id: 1, basename: "data.csv" }],
        })
        .mockResolvedValueOnce({
          results: [
            { id: 5, name: "config1", recommender_class_name: "IALSRecommender" },
          ],
        })
        // submitTraining POST
        .mockResolvedValueOnce({ id: 99 });

      const wrapper = mountPage();
      await flushPromises();

      // Select data (first select)
      const selects = wrapper.findAll(".select-stub");
      await selects[0].setValue(1);
      await nextTick();

      // Select config (second select)
      await selects[1].setValue(5);
      await nextTick();

      // Click the Start Training button
      const submitBtn = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Start Training"));
      expect(submitBtn).toBeDefined();
      await submitBtn!.trigger("click");
      await flushPromises();

      // Verify POST was made with correct body
      expect(apiMock).toHaveBeenCalledTimes(3);
      expect(apiMock).toHaveBeenCalledWith(
        expect.stringContaining("trained_model"),
        expect.objectContaining({
          method: "POST",
          body: { data_loc: 1, configuration: 5 },
        }),
      );
      expect(notifyMock.success).toHaveBeenCalledWith("Training started");
      expect(mockPush).toHaveBeenCalledWith("/projects/1/models/99");
    });

    it("shows error on training submission failure", async () => {
      apiMock
        .mockResolvedValueOnce({
          results: [{ id: 1, basename: "data.csv" }],
        })
        .mockResolvedValueOnce({
          results: [
            { id: 5, name: "config1", recommender_class_name: "IALSRecommender" },
          ],
        })
        // submitTraining POST fails
        .mockRejectedValueOnce(new Error("Server error"));

      const wrapper = mountPage();
      await flushPromises();

      // Select data and config
      const selects = wrapper.findAll(".select-stub");
      await selects[0].setValue(1);
      await nextTick();
      await selects[1].setValue(5);
      await nextTick();

      const submitBtn = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Start Training"));
      await submitBtn!.trigger("click");
      await flushPromises();

      expect(notifyMock.error).toHaveBeenCalledWith("Failed to start training");
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("does nothing when form fields are empty", async () => {
      apiMock
        .mockResolvedValueOnce({
          results: [{ id: 1, basename: "data.csv" }],
        })
        .mockResolvedValueOnce({
          results: [
            { id: 5, name: "config1", recommender_class_name: "IALSRecommender" },
          ],
        });

      const wrapper = mountPage();
      await flushPromises();

      // Do NOT select any data or config — form.data and form.config remain null
      // The button should be disabled, but let's also verify the function guard
      const submitBtn = wrapper
        .findAll("button")
        .find((b) => b.text().includes("Start Training"));
      expect(submitBtn).toBeDefined();

      // Force click even though button is disabled (to test the function guard)
      await submitBtn!.trigger("click");
      await flushPromises();

      // Only the 2 onMounted API calls should have been made (no POST)
      expect(apiMock).toHaveBeenCalledTimes(2);
      expect(notifyMock.success).not.toHaveBeenCalled();
      expect(notifyMock.error).not.toHaveBeenCalled();
    });
  });
});
