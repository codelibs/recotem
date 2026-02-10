import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "@/stores/auth";

// Mock all lazy-loaded page and layout components
const mockComponent = { template: "<div />" };
vi.mock("@/layouts/AuthLayout.vue", () => ({ default: mockComponent }));
vi.mock("@/layouts/MainLayout.vue", () => ({ default: mockComponent }));
vi.mock("@/layouts/ProjectLayout.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/LoginPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/ProjectListPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/DashboardPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/DataListPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/DataUploadPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/DataDetailPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/TuningJobListPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/TuningWizardPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/TuningJobDetailPage.vue", () => ({
  default: mockComponent,
}));
vi.mock("@/pages/ModelConfigPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/ModelComparisonPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/ModelListPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/ModelTrainPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/ModelDetailPage.vue", () => ({ default: mockComponent }));
vi.mock("@/pages/NotFoundPage.vue", () => ({ default: mockComponent }));

import router from "@/router";

describe("router", () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    localStorage.clear();
    sessionStorage.clear();
    // Reset router to a known state
    await router.push("/");
    await router.isReady();
  });

  it("redirects unauthenticated user to login", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = null;
    await router.push("/projects");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("redirects authenticated user from login to /", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/login");
    // Should redirect away from login
    expect(router.currentRoute.value.path).not.toBe("/login");
  });

  it("redirects to not-found for invalid numeric param", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/abc");
    expect(router.currentRoute.value.name).toBe("not-found");
  });

  it("saves projectId to localStorage after navigation", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/42");
    expect(localStorage.getItem("lastProjectId")).toBe("42");
  });

  it("allows authenticated user to access /projects", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects");
    expect(router.currentRoute.value.path).toBe("/projects");
  });

  it("redirects to not-found for invalid dataId param", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/data/xyz");
    expect(router.currentRoute.value.name).toBe("not-found");
  });

  it("allows valid numeric params through the guard", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/data/99");
    expect(router.currentRoute.value.name).not.toBe("not-found");
  });

  it("redirects / to last visited project when set", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    localStorage.setItem("lastProjectId", "7");
    await router.push("/");
    expect(router.currentRoute.value.path).toBe("/projects/7");
  });

  it("redirects / to /projects when no last project", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    localStorage.removeItem("lastProjectId");
    await router.push("/");
    expect(router.currentRoute.value.path).toBe("/projects");
  });

  it("stores redirect query param when redirecting unauthenticated user", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = null;
    await router.push("/projects/5/data");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/projects/5/data");
  });

  it("redirects unauthenticated user to login with redirect query for deep routes", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = null;
    await router.push("/projects/1/tuning/3");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/projects/1/tuning/3");
  });

  it("allows unauthenticated user to access not-found page", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = null;
    await router.push("/some-random-nonexistent-page");
    expect(router.currentRoute.value.name).toBe("not-found");
  });

  it("redirects to not-found for non-numeric modelId", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/models/abc");
    expect(router.currentRoute.value.name).toBe("not-found");
  });

  it("redirects to not-found for non-numeric jobId", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/tuning/xyz");
    expect(router.currentRoute.value.name).toBe("not-found");
  });

  it("allows authenticated user to access project dashboard", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/42");
    expect(router.currentRoute.value.name).toBe("project-dashboard");
    expect(router.currentRoute.value.params.projectId).toBe("42");
  });

  it("allows authenticated user to access tuning detail with valid params", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/tuning/10");
    expect(router.currentRoute.value.name).toBe("tuning-detail");
    expect(router.currentRoute.value.params.jobId).toBe("10");
  });

  it("allows authenticated user to access model detail with valid params", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/models/5");
    expect(router.currentRoute.value.name).toBe("model-detail");
    expect(router.currentRoute.value.params.modelId).toBe("5");
  });

  it("resolves data-list route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/data");
    expect(router.currentRoute.value.name).toBe("data-list");
  });

  it("resolves data-upload route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/data/upload");
    expect(router.currentRoute.value.name).toBe("data-upload");
  });

  it("resolves tuning-list route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/tuning");
    expect(router.currentRoute.value.name).toBe("tuning-list");
  });

  it("resolves tuning-new route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/tuning/new");
    expect(router.currentRoute.value.name).toBe("tuning-new");
  });

  it("resolves model-configs route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/model-configs");
    expect(router.currentRoute.value.name).toBe("model-configs");
  });

  it("resolves model-comparison route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/model-comparison");
    expect(router.currentRoute.value.name).toBe("model-comparison");
  });

  it("resolves model-list route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/models");
    expect(router.currentRoute.value.name).toBe("model-list");
  });

  it("resolves model-train route", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    await router.push("/projects/1/models/train");
    expect(router.currentRoute.value.name).toBe("model-train");
  });

  it("redirects authenticated user from login to root", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "valid-token";
    localStorage.removeItem("lastProjectId");
    await router.push("/login");
    // Should be redirected away from login to /projects
    expect(router.currentRoute.value.path).toBe("/projects");
  });
});
