import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import DataUploadPage from "@/pages/DataUploadPage.vue";
import PrimeVue from "primevue/config";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

function mountPage() {
  return mount(DataUploadPage, {
    global: {
      plugins: [
        PrimeVue,
        createPinia(),
      ],
    },
  });
}

describe("DataUploadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders upload heading", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toContain("Upload");
  });

  it("has file input", () => {
    const wrapper = mountPage();
    expect(wrapper.find('input[type="file"]').exists()).toBe(true);
  });
});
