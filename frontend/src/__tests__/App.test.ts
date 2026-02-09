import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

// vi.mock factories are hoisted - cannot reference outer variables
vi.mock("@/router", () => ({
  default: {
    beforeEach: vi.fn(() => vi.fn()),
    afterEach: vi.fn(() => vi.fn()),
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {} }),
  RouterView: { template: '<div class="router-view" />' },
}));

import App from "@/App.vue";
import router from "@/router";

function mountApp() {
  return mount(App, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Button: {
          template:
            '<button @click="$attrs.onClick?.()">{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Toast: { template: "<div />" },
        RouterView: { template: '<div class="router-view" />' },
      },
    },
  });
}

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not show loading bar initially", () => {
    const wrapper = mountApp();
    expect(wrapper.find(".route-loading-bar").exists()).toBe(false);
  });

  it("does not show error state initially", () => {
    const wrapper = mountApp();
    expect(wrapper.find(".pi-exclamation-triangle").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Something went wrong");
  });

  it("renders router-view when no error", () => {
    const wrapper = mountApp();
    expect(wrapper.find(".router-view").exists()).toBe(true);
  });

  it("registers beforeEach and afterEach hooks on router", () => {
    mountApp();
    expect(router.beforeEach).toHaveBeenCalledOnce();
    expect(router.afterEach).toHaveBeenCalledOnce();
  });

  it("shows loading bar when route navigation starts", async () => {
    const routerMod = await import("@/router");
    const wrapper = mountApp();
    const beforeEachCb = (routerMod.default.beforeEach as any).mock
      .calls[0][0];
    const next = vi.fn();
    beforeEachCb({}, {}, next);
    await nextTick();
    expect(wrapper.find(".route-loading-bar").exists()).toBe(true);
    expect(next).toHaveBeenCalled();
  });

  it("hides loading bar after route navigation completes", async () => {
    vi.useFakeTimers();
    const routerMod = await import("@/router");
    const wrapper = mountApp();
    const beforeEachCb = (routerMod.default.beforeEach as any).mock
      .calls[0][0];
    const afterEachCb = (routerMod.default.afterEach as any).mock
      .calls[0][0];
    beforeEachCb({}, {}, vi.fn());
    await nextTick();
    afterEachCb();
    vi.advanceTimersByTime(300);
    await nextTick();
    expect(wrapper.find(".route-loading-bar").exists()).toBe(false);
    vi.useRealTimers();
  });

  it("shows error state when a child component throws", async () => {
    const ErrorChild = defineComponent({
      setup() { throw new Error("test error"); },
      template: "<div />",
    });

    const wrapper = mount(App, {
      global: {
        plugins: [PrimeVue, createPinia(), i18n],
        stubs: {
          Button: {
            template: '<button @click="$attrs.onClick?.()">{{ $attrs.label }}</button>',
            inheritAttrs: false,
          },
          Toast: { template: "<div />" },
          RouterView: ErrorChild,
        },
      },
    });
    await nextTick();

    expect(wrapper.find(".pi-exclamation-triangle").exists()).toBe(true);
  });

  it("calls window.location.reload when refresh button is clicked in error state", async () => {
    const ErrorChild = defineComponent({
      setup() { throw new Error("test error"); },
      template: "<div />",
    });

    const reloadMock = vi.fn();
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...window.location, reload: reloadMock },
    });

    const wrapper = mount(App, {
      global: {
        plugins: [PrimeVue, createPinia(), i18n],
        stubs: {
          Button: {
            template: '<button @click="$attrs.onClick?.()">{{ $attrs.label }}</button>',
            inheritAttrs: false,
          },
          Toast: { template: "<div />" },
          RouterView: ErrorChild,
        },
      },
    });
    await nextTick();

    expect(wrapper.find(".pi-exclamation-triangle").exists()).toBe(true);

    // Find the "Refresh Page" button and click it
    const refreshBtn = wrapper.findAll("button").find(b => b.text().includes("Refresh Page"));
    expect(refreshBtn).toBeDefined();
    await refreshBtn!.trigger("click");

    expect(reloadMock).toHaveBeenCalled();
  });

  it("cleans up route hooks on unmount", async () => {
    const routerMod = await import("@/router");
    const removeBeforeEach = vi.fn();
    const removeAfterEach = vi.fn();
    (routerMod.default.beforeEach as any).mockReturnValue(removeBeforeEach);
    (routerMod.default.afterEach as any).mockReturnValue(removeAfterEach);

    const wrapper = mountApp();
    wrapper.unmount();

    expect(removeBeforeEach).toHaveBeenCalled();
    expect(removeAfterEach).toHaveBeenCalled();
  });
});
