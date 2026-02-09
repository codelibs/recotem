import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import { describe, expect, it } from "vitest";

import AppCard from "@/components/common/AppCard.vue";
import i18n from "@/i18n";

function mountComponent(
  props?: InstanceType<typeof AppCard>["$props"],
  slots?: Record<string, string>,
) {
  return mount(AppCard, {
    props,
    slots,
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
    },
  });
}

describe("AppCard", () => {
  it("renders title in h3 when title prop provided", () => {
    const wrapper = mountComponent({ title: "Card Title" });
    expect(wrapper.find("h3").text()).toBe("Card Title");
  });

  it("renders default slot content in body", () => {
    const wrapper = mountComponent({}, { default: "<p>Body content</p>" });
    expect(wrapper.find("p").text()).toBe("Body content");
  });

  it("does not render header when no title and no header slot", () => {
    const wrapper = mountComponent();
    expect(wrapper.find("h3").exists()).toBe(false);
    expect(wrapper.find(".border-b").exists()).toBe(false);
  });

  it("renders footer slot when provided", () => {
    const wrapper = mountComponent(
      {},
      { footer: "<span>Footer content</span>" },
    );
    expect(wrapper.find(".border-t").exists()).toBe(true);
    expect(wrapper.text()).toContain("Footer content");
  });

  it("does not render footer section when no footer slot", () => {
    const wrapper = mountComponent({ title: "Title" });
    expect(wrapper.find(".border-t").exists()).toBe(false);
  });
});
