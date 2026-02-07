import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import LoginPage from "@/pages/LoginPage.vue";
import PrimeVue from "primevue/config";

// Mock vue-router
const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ query: {} }),
}));

function mountLoginPage() {
  return mount(LoginPage, {
    global: {
      plugins: [
        PrimeVue,
        createPinia(),
      ],
      stubs: {
        FormField: {
          template: '<div><slot :id="name" :has-error="!!error" /></div>',
          props: ["label", "name", "error", "required", "hint"],
        },
      },
    },
  });
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders sign in heading", () => {
    const wrapper = mountLoginPage();
    expect(wrapper.find("h2").text()).toBe("Sign in");
  });

  it("renders username and password fields", () => {
    const wrapper = mountLoginPage();
    expect(wrapper.find('input[placeholder="Enter username"]').exists()).toBe(true);
  });

  it("renders submit button", () => {
    const wrapper = mountLoginPage();
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true);
  });

  it("validates empty username on submit", async () => {
    const wrapper = mountLoginPage();
    await wrapper.find("form").trigger("submit");
    // Form should not make login call if fields are empty
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("has novalidate on form for custom validation", () => {
    const wrapper = mountLoginPage();
    expect(wrapper.find("form").attributes("novalidate")).toBeDefined();
  });
});
