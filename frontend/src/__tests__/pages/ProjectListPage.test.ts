import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import ProjectListPage from "@/pages/ProjectListPage.vue";
import PrimeVue from "primevue/config";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {} }),
}));

vi.mock("@/api/client", () => ({
  api: vi.fn().mockResolvedValue({ results: [], count: 0 }),
}));

function mountPage() {
  return mount(ProjectListPage, {
    global: {
      plugins: [
        PrimeVue,
        createTestingPinia({ createSpy: vi.fn }),
      ],
      stubs: {
        Dialog: true,
        ProjectCreateForm: true,
      },
    },
  });
}

describe("ProjectListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders page heading", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toContain("Projects");
  });

  it("renders create project button", () => {
    const wrapper = mountPage();
    const buttons = wrapper.findAll("button");
    const createBtn = buttons.find((b) => b.text().includes("New Project") || b.text().includes("Create"));
    expect(createBtn).toBeTruthy();
  });
});
