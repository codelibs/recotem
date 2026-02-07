import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    component: () => import("@/layouts/AuthLayout.vue"),
    meta: { requiresAuth: false },
    children: [
      {
        path: "",
        name: "login",
        component: () => import("@/pages/LoginPage.vue"),
      },
    ],
  },
  {
    path: "/",
    component: () => import("@/layouts/MainLayout.vue"),
    meta: { requiresAuth: true },
    children: [
      {
        path: "",
        redirect: () => {
          // Redirect to last visited project or project list
          const lastProjectId = localStorage.getItem("lastProjectId");
          if (lastProjectId) {
            return `/projects/${lastProjectId}`;
          }
          return "/projects";
        },
      },
      {
        path: "projects",
        name: "project-list",
        component: () => import("@/pages/ProjectListPage.vue"),
      },
      {
        path: "projects/:projectId",
        component: () => import("@/layouts/ProjectLayout.vue"),
        children: [
          {
            path: "",
            name: "project-dashboard",
            component: () => import("@/pages/DashboardPage.vue"),
          },
          {
            path: "data",
            name: "data-list",
            component: () => import("@/pages/DataListPage.vue"),
          },
          {
            path: "data/upload",
            name: "data-upload",
            component: () => import("@/pages/DataUploadPage.vue"),
          },
          {
            path: "data/:dataId",
            name: "data-detail",
            component: () => import("@/pages/DataDetailPage.vue"),
          },
          {
            path: "tuning",
            name: "tuning-list",
            component: () => import("@/pages/TuningJobListPage.vue"),
          },
          {
            path: "tuning/new",
            name: "tuning-new",
            component: () => import("@/pages/TuningWizardPage.vue"),
          },
          {
            path: "tuning/:jobId",
            name: "tuning-detail",
            component: () => import("@/pages/TuningJobDetailPage.vue"),
          },
          {
            path: "models",
            name: "model-list",
            component: () => import("@/pages/ModelListPage.vue"),
          },
          {
            path: "models/train",
            name: "model-train",
            component: () => import("@/pages/ModelTrainPage.vue"),
          },
          {
            path: "models/:modelId",
            name: "model-detail",
            component: () => import("@/pages/ModelDetailPage.vue"),
          },
        ],
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});

// Navigation guard for authentication
router.beforeEach((to, from, next) => {
  const authStore = useAuthStore();
  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth !== false);

  if (requiresAuth && !authStore.isAuthenticated) {
    // User is not authenticated, redirect to login with return URL
    next({
      path: "/login",
      query: { redirect: to.fullPath },
    });
  } else if (to.path === "/login" && authStore.isAuthenticated) {
    // User is already authenticated, redirect to home
    next("/");
  } else {
    // Proceed with navigation
    next();
  }
});

// Save last visited project ID
router.afterEach((to) => {
  const projectId = to.params.projectId as string | undefined;
  if (projectId) {
    localStorage.setItem("lastProjectId", projectId);
  }
});

export default router;
