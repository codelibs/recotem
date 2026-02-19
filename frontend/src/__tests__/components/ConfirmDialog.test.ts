import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";

function mountDialog(
  props: Record<string, unknown> = {},
  modelValue = false,
) {
  return mount(ConfirmDialog, {
    props: {
      visible: modelValue,
      ...props,
    },
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Dialog: {
          template: `
            <div v-if="visible" role="alertdialog">
              <div class="dialog-header">{{ header }}</div>
              <slot />
              <slot name="footer" />
            </div>
          `,
          props: ["visible", "header", "modal", "closable"],
          emits: ["update:visible"],
        },
        Button: {
          template: '<button @click="$attrs.onClick?.()">{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
      },
    },
  });
}

describe("ConfirmDialog", () => {
  it("does not render when visible is false", () => {
    const wrapper = mountDialog({}, false);
    expect(wrapper.find('[role="alertdialog"]').exists()).toBe(false);
  });

  it("renders when visible is true", () => {
    const wrapper = mountDialog({}, true);
    expect(wrapper.find('[role="alertdialog"]').exists()).toBe(true);
  });

  it("renders header text", () => {
    const wrapper = mountDialog({ header: "Delete Item" }, true);
    expect(wrapper.text()).toContain("Delete Item");
  });

  it("renders message text", () => {
    const wrapper = mountDialog({ message: "Are you sure?" }, true);
    expect(wrapper.text()).toContain("Are you sure?");
  });

  it("renders confirm button with confirmLabel", () => {
    const wrapper = mountDialog({ confirmLabel: "Yes, delete" }, true);
    expect(wrapper.text()).toContain("Yes, delete");
  });

  it("renders cancel button with cancelLabel", () => {
    const wrapper = mountDialog({ cancelLabel: "No, keep" }, true);
    expect(wrapper.text()).toContain("No, keep");
  });

  it("emits confirm event when confirm button clicked", async () => {
    const wrapper = mountDialog({ confirmLabel: "Confirm" }, true);
    const buttons = wrapper.findAll("button");
    const confirmBtn = buttons.find(b => b.text() === "Confirm");
    expect(confirmBtn).toBeDefined();
    await confirmBtn!.trigger("click");
    expect(wrapper.emitted("confirm")).toBeTruthy();
  });

  it("emits cancel event when cancel button clicked", async () => {
    const wrapper = mountDialog({ cancelLabel: "Cancel" }, true);
    const buttons = wrapper.findAll("button");
    const cancelBtn = buttons.find(b => b.text() === "Cancel");
    expect(cancelBtn).toBeDefined();
    await cancelBtn!.trigger("click");
    expect(wrapper.emitted("cancel")).toBeTruthy();
  });

  it("emits update:visible=false when confirm is clicked", async () => {
    const wrapper = mountDialog({ confirmLabel: "OK" }, true);
    const buttons = wrapper.findAll("button");
    const confirmBtn = buttons.find(b => b.text() === "OK");
    await confirmBtn!.trigger("click");
    expect(wrapper.emitted("update:visible")).toBeTruthy();
    expect(wrapper.emitted("update:visible")![0]).toEqual([false]);
  });

  it("emits update:visible=false when cancel is clicked", async () => {
    const wrapper = mountDialog({ cancelLabel: "Cancel" }, true);
    const buttons = wrapper.findAll("button");
    const cancelBtn = buttons.find(b => b.text() === "Cancel");
    await cancelBtn!.trigger("click");
    expect(wrapper.emitted("update:visible")![0]).toEqual([false]);
  });
});
