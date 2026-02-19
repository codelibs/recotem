import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import LoginPage from "@/pages/LoginPage.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import { useAuthStore } from "@/stores/auth";

// Mock vue-router — must include createRouter/createWebHistory because
// LoginPage imports isSafeRedirect from @/router/index.ts which calls
// createRouter() at module level.
const mockPush = vi.fn();
vi.mock("vue-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("vue-router")>();
  return {
    ...actual,
    useRouter: () => ({ push: mockPush }),
    useRoute: () => ({ query: {} }),
  };
});

let pinia: ReturnType<typeof createPinia>;

function mountLoginPage() {
  pinia = createPinia();
  setActivePinia(pinia);
  return mount(LoginPage, {
    global: {
      plugins: [
        PrimeVue,
        pinia,
        i18n,
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

  it("calls authStore.login and redirects on successful login", async () => {
    const wrapper = mountLoginPage();
    const store = useAuthStore();
    vi.spyOn(store, "login").mockResolvedValue(undefined);
    // Fill in username and password
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("admin");
    await inputs[1].setValue("password123");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(store.login).toHaveBeenCalledWith("admin", "password123");
    expect(mockPush).toHaveBeenCalledWith("/projects");
  });

  it("shows error message on failed login", async () => {
    const wrapper = mountLoginPage();
    const store = useAuthStore();
    vi.spyOn(store, "login").mockRejectedValue(new Error("Invalid"));
    const inputs = wrapper.findAll("input");
    await inputs[0].setValue("admin");
    await inputs[1].setValue("wrong");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("Invalid");
  });
});
