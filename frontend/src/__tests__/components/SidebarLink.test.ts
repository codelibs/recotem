import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SidebarLink from "@/components/layout/SidebarLink.vue";
import i18n from "@/i18n";

let mockPath = "/projects";
vi.mock("vue-router", () => ({
  useRoute: () => ({ path: mockPath }),
}));

const RouterLinkStub = {
  template: '<a :class="$attrs.class"><slot /></a>',
  inheritAttrs: false,
};

function mountComponent(props: InstanceType<typeof SidebarLink>["$props"]) {
  return mount(SidebarLink, {
    props,
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        "router-link": RouterLinkStub,
      },
    },
  });
}

describe("SidebarLink", () => {
  beforeEach(() => {
    mockPath = "/projects";
  });

  it("renders label text when not collapsed", () => {
    const wrapper = mountComponent({
      to: "/projects",
      icon: "pi-folder",
      label: "Projects",
      collapsed: false,
    });
    const span = wrapper.find("span");
    expect(span.text()).toBe("Projects");
    expect(span.isVisible()).toBe(true);
  });

  it("hides label when collapsed", () => {
    const wrapper = mountComponent({
      to: "/projects",
      icon: "pi-folder",
      label: "Projects",
      collapsed: true,
    });
    const span = wrapper.find("span");
    expect(span.exists()).toBe(true);
    expect(span.isVisible()).toBe(false);
  });

  it("applies active class when route.path matches to prop exactly", () => {
    mockPath = "/projects";
    const wrapper = mountComponent({
      to: "/projects",
      icon: "pi-folder",
      label: "Projects",
      collapsed: false,
    });
    expect(wrapper.find("a").classes().join(" ")).toContain("bg-primary");
  });

  it("applies active class when route.path starts with to + '/'", () => {
    mockPath = "/projects/123";
    const wrapper = mountComponent({
      to: "/projects",
      icon: "pi-folder",
      label: "Projects",
      collapsed: false,
    });
    expect(wrapper.find("a").classes().join(" ")).toContain("bg-primary");
  });

  it("does not apply active class when route.path doesn't match", () => {
    mockPath = "/settings";
    const wrapper = mountComponent({
      to: "/projects",
      icon: "pi-folder",
      label: "Projects",
      collapsed: false,
    });
    expect(wrapper.find("a").classes().join(" ")).not.toContain("bg-primary");
  });

  it("renders icon class", () => {
    const wrapper = mountComponent({
      to: "/projects",
      icon: "pi-folder",
      label: "Projects",
      collapsed: false,
    });
    const icon = wrapper.find("i");
    expect(icon.classes()).toContain("pi");
    expect(icon.classes()).toContain("pi-folder");
  });
});
