import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import FormField from "@/components/common/FormField.vue";

describe("FormField", () => {
  it("renders label when provided", () => {
    const wrapper = mount(FormField, {
      props: { label: "Username", name: "username" },
      slots: { default: '<input type="text" />' },
    });
    expect(wrapper.find("label").text()).toContain("Username");
  });

  it("shows required indicator when required prop is true", () => {
    const wrapper = mount(FormField, {
      props: { label: "Email", name: "email", required: true },
      slots: { default: '<input type="text" />' },
    });
    expect(wrapper.find("label").text()).toContain("*");
  });

  it("does not show required indicator when not required", () => {
    const wrapper = mount(FormField, {
      props: { label: "Optional", name: "optional" },
      slots: { default: '<input type="text" />' },
    });
    expect(wrapper.find("label").text()).not.toContain("*");
  });

  it("displays error message when error prop is set", () => {
    const wrapper = mount(FormField, {
      props: { label: "Name", name: "name", error: "Name is required" },
      slots: { default: '<input type="text" />' },
    });
    expect(wrapper.find('[role="alert"]').text()).toBe("Name is required");
  });

  it("displays hint when no error", () => {
    const wrapper = mount(FormField, {
      props: { label: "Name", name: "name", hint: "Enter your name" },
      slots: { default: '<input type="text" />' },
    });
    expect(wrapper.text()).toContain("Enter your name");
  });

  it("hides hint when error is shown", () => {
    const wrapper = mount(FormField, {
      props: { label: "Name", name: "name", error: "Required", hint: "Enter your name" },
      slots: { default: '<input type="text" />' },
    });
    expect(wrapper.text()).toContain("Required");
    expect(wrapper.text()).not.toContain("Enter your name");
  });

  it("generates id from name prop", () => {
    const wrapper = mount(FormField, {
      props: { label: "Email", name: "email" },
      slots: { default: '<input type="text" />' },
    });
    expect(wrapper.find("label").attributes("for")).toBe("field-email");
  });

  it("passes id and hasError through scoped slot", () => {
    const wrapper = mount(FormField, {
      props: { label: "Test", name: "test", error: "Err" },
      slots: {
        default: `<template #default="{ id, hasError }">
          <span data-testid="slot-id">{{ id }}</span>
          <span data-testid="slot-error">{{ hasError }}</span>
        </template>`,
      },
    });
    expect(wrapper.find('[data-testid="slot-id"]').text()).toBe("field-test");
    expect(wrapper.find('[data-testid="slot-error"]').text()).toBe("true");
  });
});
