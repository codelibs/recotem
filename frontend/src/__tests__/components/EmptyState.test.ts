import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import EmptyState from "@/components/common/EmptyState.vue";

function mountComponent(
  props: InstanceType<typeof EmptyState>["$props"],
  slots?: Record<string, string>,
) {
  return mount(EmptyState, {
    props,
    slots,
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
    },
  });
}

describe("EmptyState", () => {
  it("renders title text", () => {
    const wrapper = mountComponent({ icon: "pi-inbox", title: "No items found" });
    expect(wrapper.find("h3").text()).toBe("No items found");
  });

  it("renders icon class on <i> element", () => {
    const wrapper = mountComponent({ icon: "pi-inbox", title: "Empty" });
    const icon = wrapper.find("i");
    expect(icon.classes()).toContain("pi");
    expect(icon.classes()).toContain("pi-inbox");
  });

  it("renders description when provided", () => {
    const wrapper = mountComponent({
      icon: "pi-inbox",
      title: "No data",
      description: "Upload a file to get started.",
    });
    expect(wrapper.text()).toContain("Upload a file to get started.");
  });

  it("does not render description paragraph when not provided", () => {
    const wrapper = mountComponent({ icon: "pi-inbox", title: "Empty" });
    expect(wrapper.find("p").exists()).toBe(false);
  });

  it("renders default slot content", () => {
    const wrapper = mountComponent(
      { icon: "pi-inbox", title: "Empty" },
      { default: '<button class="action-btn">Add item</button>' },
    );
    expect(wrapper.find(".action-btn").exists()).toBe(true);
    expect(wrapper.find(".action-btn").text()).toBe("Add item");
  });

  it("renders both description and slot when both provided", () => {
    const wrapper = mountComponent(
      { icon: "pi-inbox", title: "Empty", description: "No items here." },
      { default: "<span class='cta'>Create one</span>" },
    );
    expect(wrapper.text()).toContain("No items here.");
    expect(wrapper.find(".cta").exists()).toBe(true);
  });
});
