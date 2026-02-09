import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import { describe, expect, it, vi } from "vitest";

import PageHeader from "@/components/common/PageHeader.vue";
import i18n from "@/i18n";

const mockPush = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mockPush }),
  useRoute: () => ({ params: {} }),
}));

function mountComponent(
  props?: InstanceType<typeof PageHeader>["$props"],
  slots?: Record<string, string>,
) {
  return mount(PageHeader, {
    props,
    slots,
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      mocks: { $router: { push: mockPush } },
      stubs: {
        Button: {
          template:
            '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        RouterLink: { template: "<a><slot /></a>" },
      },
    },
  });
}

describe("PageHeader", () => {
  it("renders title text in h2", () => {
    const wrapper = mountComponent({ title: "Page Title" });
    expect(wrapper.find("h2").text()).toBe("Page Title");
  });

  it("renders back button when backTo prop provided", () => {
    const wrapper = mountComponent({ backTo: "/projects" });
    expect(wrapper.find("button").exists()).toBe(true);
  });

  it("does not render back button when backTo is not provided", () => {
    const wrapper = mountComponent({ title: "Title" });
    expect(wrapper.find("button").exists()).toBe(false);
  });

  it("renders actions slot content", () => {
    const wrapper = mountComponent(
      { title: "Title" },
      { actions: "<button>Action</button>" },
    );
    expect(wrapper.text()).toContain("Action");
  });

  it("renders back button that is clickable when backTo provided", async () => {
    const wrapper = mountComponent({ backTo: "/projects" });
    const btn = wrapper.find("button");
    expect(btn.exists()).toBe(true);
    // The onClick triggers $router.push(backTo) — verify button renders with the click handler
    await btn.trigger("click");
    // Router.push is called via $router in template, which goes through the mock
    expect(mockPush).toHaveBeenCalledWith("/projects");
  });
});
