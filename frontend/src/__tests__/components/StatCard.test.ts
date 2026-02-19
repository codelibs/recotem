import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import StatCard from "@/components/common/StatCard.vue";

function mountComponent(props: InstanceType<typeof StatCard>["$props"]) {
  return mount(StatCard, {
    props,
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        RouterLink: {
          template: '<a :href="to"><slot /></a>',
          props: ["to"],
        },
      },
    },
  });
}

describe("StatCard", () => {
  it("renders the value", () => {
    const wrapper = mountComponent({ icon: "pi-database", label: "Datasets", value: 5, to: "/data" });
    expect(wrapper.text()).toContain("5");
  });

  it("renders string value", () => {
    const wrapper = mountComponent({ icon: "pi-database", label: "Status", value: "Active", to: "/data" });
    expect(wrapper.text()).toContain("Active");
  });

  it("renders the label", () => {
    const wrapper = mountComponent({ icon: "pi-database", label: "Datasets", value: 3, to: "/data" });
    expect(wrapper.text()).toContain("Datasets");
  });

  it("renders icon class on <i> element", () => {
    const wrapper = mountComponent({ icon: "pi-database", label: "Datasets", value: 3, to: "/data" });
    const icon = wrapper.find("i");
    expect(icon.classes()).toContain("pi");
    expect(icon.classes()).toContain("pi-database");
  });

  it("wraps content in a router-link with the to prop", () => {
    const wrapper = mountComponent({ icon: "pi-database", label: "Models", value: 2, to: "/models" });
    const link = wrapper.find("a");
    expect(link.exists()).toBe(true);
    expect(link.attributes("href")).toBe("/models");
  });

  it("renders value 0 correctly", () => {
    const wrapper = mountComponent({ icon: "pi-database", label: "Items", value: 0, to: "/items" });
    expect(wrapper.text()).toContain("0");
  });
});
