import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import ProjectListPage from "@/pages/ProjectListPage.vue";
import { useAuthStore } from "@/stores/auth";
import PrimeVue from "primevue/config";
import i18n from "@/i18n";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {} }),
}));

const toastAddMock = vi.fn();
vi.mock("primevue/usetoast", () => ({
  useToast: () => ({ add: toastAddMock }),
}));

const apiMock = vi.fn();
vi.mock("@/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  unwrapResults: (res: any) => Array.isArray(res) ? res : res.results,
  classifyApiError: (err: unknown) => ({
    kind: "unknown",
    status: null,
    message: err instanceof Error ? err.message : "Unknown error",
    raw: err,
  }),
}));

function mountPage() {
  return mount(ProjectListPage, {
    global: {
      plugins: [PrimeVue, createPinia(), i18n],
      stubs: {
        Dialog: true,
        ProjectCreateForm: true,
        ConfirmDialog: {
          template: '<div class="confirm-dialog" v-if="visible"><span class="confirm-message">{{ message }}</span><button class="confirm-btn" @click="$emit(\'confirm\')">Confirm</button></div>',
          props: ["visible", "header", "message", "confirmLabel", "cancelLabel", "danger"],
          emits: ["confirm", "cancel", "update:visible"],
        },
        Menu: {
          template: '<div class="popup-menu" />',
          methods: { toggle() {} },
        },
        EmptyState: { template: '<div class="empty-state">{{ title }} {{ description }}<slot /></div>', props: ["icon", "title", "description"] },
        InputText: {
          template: '<input class="search-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
          props: ["modelValue"],
          emits: ["update:modelValue"],
        },
        Button: { template: '<button @click="$attrs.onClick?.()"><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
        Message: { template: '<div class="message"><slot /></div>' },
        Skeleton: { template: '<div class="skeleton" />' },
      },
    },
  });
}

const mockProjects = [
  {
    id: 1,
    name: "MovieLens Recs",
    user_column: "user_id",
    item_column: "movie_id",
    time_column: "timestamp",
    owner: 1,
    ins_datetime: "2025-01-01T00:00:00Z",
  },
  {
    id: 2,
    name: "Amazon Books",
    user_column: "customer",
    item_column: "isbn",
    time_column: null,
    owner: 1,
    ins_datetime: "2025-01-02T00:00:00Z",
  },
  {
    id: 3,
    name: "Music Playlist",
    user_column: "listener",
    item_column: "track_id",
    time_column: "played_at",
    owner: 2,
    ins_datetime: "2025-01-03T00:00:00Z",
  },
];

describe("ProjectListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setActivePinia(createPinia());
  });

  it("renders page heading", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.text()).toContain("Projects");
  });

  it("renders create project button", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();
    const buttons = wrapper.findAll("button");
    const createBtn = buttons.find((b) => b.text().includes("New Project") || b.text().includes("Create"));
    expect(createBtn).toBeTruthy();
  });

  // --- Error scenario tests ---

  it("displays error message when API fails", async () => {
    apiMock.mockRejectedValueOnce(new Error("Server error"));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(true);
    expect(wrapper.text()).toContain("Failed to load projects");
  });

  it("shows retry button in error state", async () => {
    apiMock.mockRejectedValueOnce(new Error("Connection refused"));
    const wrapper = mountPage();
    await flushPromises();

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    expect(retryBtn).toBeDefined();
  });

  it("retries fetch when retry button is clicked", async () => {
    apiMock
      .mockRejectedValueOnce(new Error("Failed"))
      .mockResolvedValueOnce({ results: mockProjects, count: 3 });

    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".message").exists()).toBe(true);

    const retryBtn = wrapper.findAll("button").find(b => b.text().includes("Retry"));
    await retryBtn!.trigger("click");
    await flushPromises();

    expect(apiMock).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("MovieLens Recs");
  });

  it("renders empty state when no projects exist", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(wrapper.text()).toContain("No projects yet");
  });

  it("renders project cards when projects exist", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("MovieLens Recs");
    expect(wrapper.text()).toContain("Amazon Books");
    expect(wrapper.text()).toContain("Music Playlist");
  });

  it("renders search input when projects exist", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".search-input").exists()).toBe(true);
  });

  it("filters projects by name when searching", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const wrapper = mountPage();
    await flushPromises();

    // All 3 projects should be visible
    expect(wrapper.text()).toContain("MovieLens Recs");
    expect(wrapper.text()).toContain("Amazon Books");
    expect(wrapper.text()).toContain("Music Playlist");

    // Type search query
    const searchInput = wrapper.find(".search-input");
    await searchInput.setValue("amazon");
    await nextTick();

    // Only Amazon Books should be visible
    expect(wrapper.text()).toContain("Amazon Books");
    expect(wrapper.text()).not.toContain("MovieLens Recs");
    expect(wrapper.text()).not.toContain("Music Playlist");
  });

  it("shows no results message when filter matches nothing", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const wrapper = mountPage();
    await flushPromises();

    const searchInput = wrapper.find(".search-input");
    await searchInput.setValue("nonexistent");
    await nextTick();

    expect(wrapper.text()).toContain("No results found");
  });

  it("restores full list when search is cleared", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const wrapper = mountPage();
    await flushPromises();

    // Filter
    const searchInput = wrapper.find(".search-input");
    await searchInput.setValue("amazon");
    await nextTick();
    expect(wrapper.text()).not.toContain("MovieLens Recs");

    // Clear filter
    await searchInput.setValue("");
    await nextTick();
    expect(wrapper.text()).toContain("MovieLens Recs");
    expect(wrapper.text()).toContain("Amazon Books");
    expect(wrapper.text()).toContain("Music Playlist");
  });

  it("search is case-insensitive", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const wrapper = mountPage();
    await flushPromises();

    const searchInput = wrapper.find(".search-input");
    await searchInput.setValue("MOVIELENS");
    await nextTick();

    expect(wrapper.text()).toContain("MovieLens Recs");
    expect(wrapper.text()).not.toContain("Amazon Books");
  });

  it("does not show search input when no projects exist", async () => {
    apiMock.mockResolvedValue({ results: [], count: 0 });
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".search-input").exists()).toBe(false);
  });

  it("does not show search input in error state", async () => {
    apiMock.mockRejectedValueOnce(new Error("Failed"));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find(".search-input").exists()).toBe(false);
  });

  it("shows loading skeletons during fetch", async () => {
    apiMock.mockReturnValue(new Promise(() => {})); // never resolves
    const wrapper = mountPage();
    await nextTick();

    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("hides skeletons after fetch completes", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.findAll(".skeleton").length).toBe(0);
  });

  // --- Delete project tests ---

  it("shows kebab menu button on owned projects", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const pinia = createPinia();
    setActivePinia(pinia);
    const authStore = useAuthStore(pinia);
    authStore.user = { pk: 1, username: "testuser", email: "test@example.com" };

    const wrapper = mount(ProjectListPage, {
      global: {
        plugins: [PrimeVue, pinia, i18n],
        stubs: {
          Dialog: true,
          ProjectCreateForm: true,
          ConfirmDialog: { template: '<div class="confirm-dialog" />', props: ["visible", "header", "message", "confirmLabel", "cancelLabel", "danger"] },
          Menu: { template: '<div class="popup-menu" />', methods: { toggle() {} } },
          EmptyState: { template: '<div class="empty-state"><slot /></div>', props: ["icon", "title", "description"] },
          InputText: { template: '<input class="search-input" />', props: ["modelValue"] },
          Button: { template: '<button><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
          Message: { template: '<div />' },
          Skeleton: { template: '<div />' },
        },
      },
    });
    await flushPromises();

    const kebabButtons = wrapper.findAll(".kebab-menu-btn");
    // Projects 1 and 2 are owned by user 1, project 3 is owned by user 2
    expect(kebabButtons.length).toBe(2);
  });

  it("does not show kebab menu on projects owned by others", async () => {
    apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
    const pinia = createPinia();
    setActivePinia(pinia);
    const authStore = useAuthStore(pinia);
    authStore.user = { pk: 99, username: "other", email: "other@example.com" };

    const wrapper = mount(ProjectListPage, {
      global: {
        plugins: [PrimeVue, pinia, i18n],
        stubs: {
          Dialog: true,
          ProjectCreateForm: true,
          ConfirmDialog: { template: '<div />', props: ["visible", "header", "message", "confirmLabel", "cancelLabel", "danger"] },
          Menu: { template: '<div />', methods: { toggle() {} } },
          EmptyState: { template: '<div class="empty-state"><slot /></div>', props: ["icon", "title", "description"] },
          InputText: { template: '<input class="search-input" />', props: ["modelValue"] },
          Button: { template: '<button><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
          Message: { template: '<div />' },
          Skeleton: { template: '<div />' },
        },
      },
    });
    await flushPromises();

    expect(wrapper.findAll(".kebab-menu-btn").length).toBe(0);
  });

  it("shows success toast after successful deletion", async () => {
    apiMock
      .mockResolvedValueOnce({ results: mockProjects, count: 3 })
      .mockResolvedValueOnce(undefined); // DELETE response

    const pinia = createPinia();
    setActivePinia(pinia);
    const authStore = useAuthStore(pinia);
    authStore.user = { pk: 1, username: "testuser", email: "test@example.com" };

    const ConfirmDialogStub = {
      name: "ConfirmDialog",
      template: '<div class="confirm-dialog"><button class="confirm-btn" @click="$emit(\'confirm\')">Confirm</button></div>',
      props: ["visible", "header", "message", "confirmLabel", "cancelLabel", "danger"],
      emits: ["confirm", "cancel", "update:visible"],
    };

    const wrapper = mount(ProjectListPage, {
      global: {
        plugins: [PrimeVue, pinia, i18n],
        stubs: {
          Dialog: true,
          ProjectCreateForm: true,
          ConfirmDialog: ConfirmDialogStub,
          Menu: { template: '<div class="popup-menu" />', methods: { toggle() {} } },
          EmptyState: { template: '<div class="empty-state"><slot /></div>', props: ["icon", "title", "description"] },
          InputText: { template: '<input class="search-input" />', props: ["modelValue"] },
          Button: { template: '<button><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
          Message: { template: '<div />' },
          Skeleton: { template: '<div />' },
        },
      },
    });
    await flushPromises();

    // Click kebab menu on first owned card to set deleteTarget
    const kebabBtn = wrapper.find(".kebab-menu-btn");
    await kebabBtn.trigger("click");
    await nextTick();

    // Click confirm button in the ConfirmDialog stub
    await wrapper.find(".confirm-btn").trigger("click");
    await flushPromises();

    expect(toastAddMock).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: "success",
        summary: "Project deleted successfully",
      }),
    );
  });

  describe("menuItems command", () => {
    it("sets showDeleteConfirm to true when menu command is triggered", async () => {
      apiMock.mockResolvedValue({ results: mockProjects, count: 3 });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      expect(vm.showDeleteConfirm).toBe(false);

      // menuItems is a computed that returns an array with a command function
      const items = vm.menuItems;
      items[0].command();

      expect(vm.showDeleteConfirm).toBe(true);
    });
  });

  describe("onProjectCreated", () => {
    it("closes create dialog when project is created", async () => {
      apiMock.mockResolvedValue({ results: [], count: 0 });
      const wrapper = mountPage();
      await flushPromises();

      const vm = wrapper.vm as any;
      vm.showCreate = true;
      await nextTick();

      vm.onProjectCreated();
      await nextTick();

      expect(vm.showCreate).toBe(false);
    });
  });

  it("shows error toast when deletion fails", async () => {
    apiMock
      .mockResolvedValueOnce({ results: mockProjects, count: 3 })
      .mockRejectedValueOnce(new Error("Server error")); // DELETE fails

    const pinia = createPinia();
    setActivePinia(pinia);
    const authStore = useAuthStore(pinia);
    authStore.user = { pk: 1, username: "testuser", email: "test@example.com" };

    const ConfirmDialogStub = {
      name: "ConfirmDialog",
      template: '<div class="confirm-dialog"><button class="confirm-btn" @click="$emit(\'confirm\')">Confirm</button></div>',
      props: ["visible", "header", "message", "confirmLabel", "cancelLabel", "danger"],
      emits: ["confirm", "cancel", "update:visible"],
    };

    const wrapper = mount(ProjectListPage, {
      global: {
        plugins: [PrimeVue, pinia, i18n],
        stubs: {
          Dialog: true,
          ProjectCreateForm: true,
          ConfirmDialog: ConfirmDialogStub,
          Menu: { template: '<div class="popup-menu" />', methods: { toggle() {} } },
          EmptyState: { template: '<div class="empty-state"><slot /></div>', props: ["icon", "title", "description"] },
          InputText: { template: '<input class="search-input" />', props: ["modelValue"] },
          Button: { template: '<button><slot />{{ $attrs.label }}</button>', inheritAttrs: false },
          Message: { template: '<div />' },
          Skeleton: { template: '<div />' },
        },
      },
    });
    await flushPromises();

    // Click kebab menu on first owned card to set deleteTarget
    const kebabBtn = wrapper.find(".kebab-menu-btn");
    await kebabBtn.trigger("click");
    await nextTick();

    // Click confirm button in the ConfirmDialog stub
    await wrapper.find(".confirm-btn").trigger("click");
    await flushPromises();

    expect(toastAddMock).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: "error",
        summary: "Failed to delete project",
      }),
    );
  });
});
