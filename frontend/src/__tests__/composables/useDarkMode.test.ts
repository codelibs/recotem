import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";

// Must mock matchMedia before importing useDarkMode,
// because the composable reads system preference at module level.
function mockMatchMedia(matches = false) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockReturnValue({ matches, addEventListener: vi.fn() }),
  });
}
mockMatchMedia(false);

// Dynamic import so the mock is in place before the module initializes.
const { useDarkMode } = await import("@/composables/useDarkMode");

const TestComponent = defineComponent({
  setup() {
    const { isDark, toggle } = useDarkMode();
    return { isDark, toggle };
  },
  template: '<div>{{ isDark ? "dark" : "light" }}</div>',
});

describe("useDarkMode", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark-mode");
    mockMatchMedia(false);
  });

  it("defaults to system preference when no stored value", async () => {
    const wrapper = mount(TestComponent);
    await flushPromises();
    expect(wrapper.text()).toBe("light");
  });

  it("reads stored preference", async () => {
    localStorage.setItem("dark-mode", "true");

    // Re-import to pick up new localStorage value
    vi.resetModules();
    mockMatchMedia(false);
    const mod = await import("@/composables/useDarkMode");
    const Comp = defineComponent({
      setup() {
        const { isDark, toggle } = mod.useDarkMode();
        return { isDark, toggle };
      },
      template: '<div>{{ isDark ? "dark" : "light" }}</div>',
    });
    const wrapper = mount(Comp);
    await flushPromises();
    expect(wrapper.text()).toBe("dark");
  });

  it("toggles dark mode", async () => {
    const wrapper = mount(TestComponent);
    await flushPromises();
    expect(wrapper.text()).toBe("light");
    (wrapper.vm as any).toggle();
    await nextTick();
    expect(wrapper.text()).toBe("dark");
    expect(localStorage.getItem("dark-mode")).toBe("true");
  });

  it("applies class to document element", async () => {
    localStorage.setItem("dark-mode", "true");

    vi.resetModules();
    mockMatchMedia(false);
    const mod = await import("@/composables/useDarkMode");
    const Comp = defineComponent({
      setup() {
        const { isDark, toggle } = mod.useDarkMode();
        return { isDark, toggle };
      },
      template: '<div>{{ isDark ? "dark" : "light" }}</div>',
    });
    mount(Comp);
    await flushPromises();
    expect(document.documentElement.classList.contains("dark-mode")).toBe(true);
  });
});
