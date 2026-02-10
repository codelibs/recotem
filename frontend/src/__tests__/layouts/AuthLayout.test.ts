import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import AuthLayout from "@/layouts/AuthLayout.vue";

function mountLayout() {
  return mount(AuthLayout, {
    global: {
      stubs: {
        RouterView: { template: '<div class="router-view" />' },
      },
    },
  });
}

describe("AuthLayout", () => {
  it("renders Recotem heading", () => {
    const wrapper = mountLayout();
    expect(wrapper.find("h1").text()).toBe("Recotem");
  });

  it("renders Recommendation System Builder description", () => {
    const wrapper = mountLayout();
    expect(wrapper.find("p").text()).toBe("Recommendation System Builder");
  });

  it("renders router-view slot", () => {
    const wrapper = mountLayout();
    expect(wrapper.find(".router-view").exists()).toBe(true);
  });
});
