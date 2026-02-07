import { describe, it, expect } from "vitest";
import { defineComponent } from "vue";
import { mount } from "@vue/test-utils";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";

describe("useAbortOnUnmount", () => {
  it("returns a controller with a signal that is not aborted initially", () => {
    const TestComponent = defineComponent({
      setup() {
        const controller = useAbortOnUnmount();
        return { controller };
      },
      template: "<div>test</div>",
    });

    const wrapper = mount(TestComponent);
    const controller = wrapper.vm.controller as AbortController;
    expect(controller.signal.aborted).toBe(false);
  });

  it("aborts the signal when component unmounts", () => {
    let capturedController: AbortController | undefined;

    const TestComponent = defineComponent({
      setup() {
        capturedController = useAbortOnUnmount();
        return { controller: capturedController };
      },
      template: "<div>test</div>",
    });

    const wrapper = mount(TestComponent);
    expect(capturedController).toBeDefined();
    expect(capturedController!.signal.aborted).toBe(false);

    wrapper.unmount();
    expect(capturedController!.signal.aborted).toBe(true);
  });

  it("works outside component setup without error", () => {
    // When called outside a component instance, it should still return a valid controller
    // but won't automatically abort (no onUnmounted hook available)
    const controller = useAbortOnUnmount();
    expect(controller).toBeDefined();
    expect(controller.signal).toBeDefined();
    expect(controller.signal.aborted).toBe(false);
  });
});
