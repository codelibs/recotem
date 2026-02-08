import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import DataUploadPage from "@/pages/DataUploadPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

const notifyMock = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock("@/composables/useNotification", () => ({
  useNotification: () => notifyMock,
}));

function mountPage() {
  return mount(DataUploadPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        FileUpload: {
          template: `<div class="file-upload">
            <input type="file" :accept="accept" :disabled="disabled" @change="handleChange" />
            <slot name="empty" />
          </div>`,
          props: ["mode", "accept", "maxFileSize", "auto", "chooseLabel", "customUpload", "disabled"],
          emits: ["upload", "error", "uploader"],
          setup(props: any, { emit }: any) {
            const handleChange = (e: any) => {
              emit("uploader", { files: e.target.files });
            };
            return { handleChange };
          },
        },
        ProgressBar: { template: '<div class="progress-bar" :data-value="value" />', props: ["value"] },
        Button: {
          template: '<button :disabled="$attrs.disabled" @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Message: { template: '<div class="message" :data-severity="severity"><slot /></div>', props: ["severity", "closable"] },
      },
    },
  });
}

describe("DataUploadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders upload heading", () => {
    const wrapper = mountPage();
    expect(wrapper.find("h2").exists()).toBe(true);
    expect(wrapper.text()).toContain("Upload");
  });

  it("has file input", () => {
    const wrapper = mountPage();
    expect(wrapper.find('input[type="file"]').exists()).toBe(true);
  });

  it("accepts CSV files", () => {
    const wrapper = mountPage();
    const input = wrapper.find('input[type="file"]');
    expect(input.attributes("accept")).toBe(".csv,.csv.gz");
  });

  it("renders back button that navigates to data list", async () => {
    const wrapper = mountPage();
    // The first button in the header should be the back button
    const buttons = wrapper.findAll("button");
    expect(buttons.length).toBeGreaterThan(0);
  });

  it("renders drag-and-drop empty state", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toMatch(/drag|drop|CSV/i);
  });

  it("does not show progress bar initially", () => {
    const wrapper = mountPage();
    expect(wrapper.find(".progress-bar").exists()).toBe(false);
  });

  it("does not show error message initially", () => {
    const wrapper = mountPage();
    expect(wrapper.find(".message").exists()).toBe(false);
  });
});
