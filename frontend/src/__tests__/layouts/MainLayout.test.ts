import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import type { Pinia } from "pinia";
import MainLayout from "@/layouts/MainLayout.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import { useAuthStore } from "@/stores/auth";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ path: "/projects", params: {} }),
}));

const mockToggleDarkMode = vi.fn();
vi.mock("@/composables/useDarkMode", () => ({
  useDarkMode: () => ({ isDark: { value: false }, toggle: mockToggleDarkMode }),
}));

vi.mock("@/i18n", async () => {
  const actual = await vi.importActual("@/i18n");
  return {
    ...actual,
    setLocale: vi.fn(),
    getLocale: () => "en",
  };
});

let pinia: Pinia;

function mountLayout() {
  return mount(MainLayout, {
    global: {
      plugins: [PrimeVue, pinia, i18n],
      stubs: {
        Button: {
          template: '<button @click="$attrs.onClick?.()">{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        SidebarLink: {
          template: '<div class="sidebar-link">{{ label }}</div>',
          props: ["to", "icon", "label", "collapsed"],
        },
        Breadcrumb: { template: '<div class="breadcrumb" />' },
        Toast: { template: '<div />' },
        RouterView: { template: '<div class="router-view" />' },
      },
    },
  });
}

describe("MainLayout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
    localStorage.clear();
  });

  it("renders Recotem text in sidebar", () => {
    const wrapper = mountLayout();
    expect(wrapper.text()).toContain("Recotem");
  });

  it("renders user avatar with first letter of username", () => {
    const store = useAuthStore();
    store.user = { username: "admin", pk: 1, email: "a@b.com" } as any;
    const wrapper = mountLayout();
    // The avatar shows the uppercase first letter
    expect(wrapper.text()).toContain("A");
  });

  it("logout button triggers authStore.logout and navigates to /login", async () => {
    const store = useAuthStore();
    store.user = { username: "admin", pk: 1, email: "a@b.com" } as any;
    vi.spyOn(store, "logout").mockResolvedValue(undefined);

    const wrapper = mountLayout();
    // Find the logout button by aria-label
    const logoutBtn = wrapper.find('button[aria-label="Logout"]');
    expect(logoutBtn.exists()).toBe(true);
    await logoutBtn.trigger("click");
    await flushPromises();

    expect(store.logout).toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledWith("/login");
  });

  it("toggleSidebar saves state to localStorage", async () => {
    const wrapper = mountLayout();
    // Find the collapse/expand button in the sidebar (hidden md:block)
    const toggleBtn = wrapper.find('button[aria-label="Collapse sidebar"]');
    expect(toggleBtn.exists()).toBe(true);

    await toggleBtn.trigger("click");
    expect(localStorage.getItem("sidebar-collapsed")).toBe("true");

    // After collapsing, the button label changes to "Expand sidebar"
    const expandBtn = wrapper.find('button[aria-label="Expand sidebar"]');
    expect(expandBtn.exists()).toBe(true);
    await expandBtn.trigger("click");
    expect(localStorage.getItem("sidebar-collapsed")).toBe("false");
  });

  it("toggleLocale calls setLocale and switches language", async () => {
    const store = useAuthStore();
    store.user = { username: "admin", pk: 1, email: "a@b.com" } as any;
    const wrapper = mountLayout();
    // The locale toggle button has aria-label="Language"
    const localeBtn = wrapper.find('button[aria-label="Language"]');
    expect(localeBtn.exists()).toBe(true);
    await localeBtn.trigger("click");
    const { setLocale } = await import("@/i18n");
    expect(setLocale).toHaveBeenCalled();
  });

  it("renders breadcrumb component", () => {
    const wrapper = mountLayout();
    expect(wrapper.find(".breadcrumb").exists()).toBe(true);
  });

  it("renders sidebar navigation links", () => {
    const wrapper = mountLayout();
    expect(wrapper.findAll(".sidebar-link").length).toBeGreaterThan(0);
  });

  it("opens mobile menu when hamburger button is clicked", async () => {
    const wrapper = mountLayout();
    const menuBtn = wrapper.find('button[aria-label="Open menu"]');
    expect(menuBtn.exists()).toBe(true);
    await menuBtn.trigger("click");
    // When mobileOpen is true, the overlay div should appear
    expect(wrapper.find(".fixed.inset-0.z-30").exists()).toBe(true);
  });

  it("closes mobile menu when overlay is clicked", async () => {
    const wrapper = mountLayout();
    // Open mobile menu
    const menuBtn = wrapper.find('button[aria-label="Open menu"]');
    await menuBtn.trigger("click");
    expect(wrapper.find(".fixed.inset-0.z-30").exists()).toBe(true);

    // Click overlay to close
    await wrapper.find(".fixed.inset-0.z-30").trigger("click");
    expect(wrapper.find(".fixed.inset-0.z-30").exists()).toBe(false);
  });

  it("closes mobile menu when close button is clicked", async () => {
    const wrapper = mountLayout();
    // Open mobile menu first
    const menuBtn = wrapper.find('button[aria-label="Open menu"]');
    await menuBtn.trigger("click");
    expect(wrapper.find(".fixed.inset-0.z-30").exists()).toBe(true);

    // Find and click the close button
    const closeBtn = wrapper.find('button[aria-label="Close menu"]');
    expect(closeBtn.exists()).toBe(true);
    await closeBtn.trigger("click");
    expect(wrapper.find(".fixed.inset-0.z-30").exists()).toBe(false);
  });

  it("toggles dark mode when theme button is clicked", async () => {
    const store = useAuthStore();
    store.user = { username: "admin", pk: 1, email: "a@b.com" } as any;
    const wrapper = mountLayout();

    // The dark mode toggle button contains a moon icon (pi-moon) when isDark is false
    const buttons = wrapper.findAll("button");
    const darkBtn = buttons.find((b) => b.find(".pi-moon").exists() || b.find(".pi-sun").exists());
    expect(darkBtn).toBeDefined();
    await darkBtn!.trigger("click");
    expect(mockToggleDarkMode).toHaveBeenCalled();
  });

  it("reads collapsed state from localStorage on mount", () => {
    localStorage.setItem("sidebar-collapsed", "true");
    const wrapper = mountLayout();
    // When collapsed, sidebar should have w-14 class
    const aside = wrapper.find("aside");
    expect(aside.classes()).toContain("w-14");
  });
});
