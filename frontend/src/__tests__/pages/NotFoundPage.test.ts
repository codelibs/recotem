import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import NotFoundPage from "@/pages/NotFoundPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ params: {} }),
}));

function mountPage() {
  return mount(NotFoundPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: {
          template:
            '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
      },
    },
  });
}

describe("NotFoundPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders 'Page Not Found' heading", () => {
    const wrapper = mountPage();
    expect(wrapper.find("h1").exists()).toBe(true);
    expect(wrapper.find("h1").text()).toContain("Page Not Found");
  });

  it("renders 'Go to Projects' button", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toContain("Go to Projects");
  });

  it("navigates to /projects when Go to Projects button is clicked", async () => {
    const wrapper = mountPage();
    const btn = wrapper.find("button");
    await btn.trigger("click");
    expect(mockPush).toHaveBeenCalledWith("/projects");
  });
});
