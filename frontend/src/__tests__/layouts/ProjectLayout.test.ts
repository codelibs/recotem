import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import type { Pinia } from "pinia";
import ProjectLayout from "@/layouts/ProjectLayout.vue";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";
import { useProjectStore } from "@/stores/project";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: { projectId: "1" } }),
}));

let pinia: Pinia;

function mountLayout() {
  return mount(ProjectLayout, {
    global: {
      plugins: [PrimeVue, pinia, i18n],
      stubs: {
        Button: {
          template: '<button @click="$attrs.onClick?.()">{{ $attrs.label }}</button>',
          inheritAttrs: false,
        },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
        RouterView: { template: '<div class="router-view" />' },
      },
    },
  });
}

describe("ProjectLayout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
  });

  it("shows skeleton when loading", async () => {
    const store = useProjectStore();
    // Mock fetchProject so it stays in loading state
    vi.spyOn(store, "fetchProject").mockImplementation(async () => {
      store.loading = true;
    });
    const wrapper = mountLayout();
    await flushPromises();
    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("shows error message when error is set", async () => {
    const store = useProjectStore();
    vi.spyOn(store, "fetchProject").mockImplementation(async () => {
      store.error = { kind: "not_found", status: 404, message: "Not found" } as any;
    });
    const wrapper = mountLayout();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Not found");
  });

  it("shows router-view when no loading and no error", async () => {
    const store = useProjectStore();
    vi.spyOn(store, "fetchProject").mockImplementation(async () => {
      store.loading = false;
      store.error = null;
      store.currentProject = { id: 1, name: "Test" } as any;
    });
    const wrapper = mountLayout();
    await flushPromises();
    expect(wrapper.find(".router-view").exists()).toBe(true);
  });

  it("calls fetchProject on mount with route param projectId", async () => {
    const store = useProjectStore();
    const fetchSpy = vi.spyOn(store, "fetchProject").mockResolvedValue(undefined);
    mountLayout();
    await flushPromises();
    expect(fetchSpy).toHaveBeenCalledWith(1);
  });

  it("retry button in error state calls fetchProject again", async () => {
    const store = useProjectStore();
    const fetchSpy = vi
      .spyOn(store, "fetchProject")
      .mockImplementationOnce(async () => {
        store.error = {
          kind: "not_found",
          status: 404,
          message: "Not found",
        } as any;
      })
      .mockImplementationOnce(async () => {
        store.loading = false;
        store.error = null;
        store.currentProject = { id: 1, name: "Test" } as any;
      });
    const wrapper = mountLayout();
    await flushPromises();
    expect(wrapper.find(".message").exists()).toBe(true);
    const retryBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});
