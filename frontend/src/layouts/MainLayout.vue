<template>
  <div class="flex h-screen overflow-hidden bg-neutral-10">
    <a href="#main-content" class="skip-link">Skip to main content</a>

    <!-- Sidebar -->
    <aside
      :class="['flex flex-col border-r border-neutral-30 bg-neutral-20 transition-all duration-200', collapsed ? 'w-14' : 'w-60']"
      role="navigation"
      aria-label="Main navigation"
    >
      <!-- Logo -->
      <div class="flex items-center h-14 px-4 border-b border-neutral-30">
        <i class="pi pi-prime text-primary text-xl" aria-hidden="true" />
        <span
          v-show="!collapsed"
          class="ml-3 font-semibold text-neutral-800 truncate"
        >Recotem</span>
        <button
          class="ml-auto p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary"
          :aria-label="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
          @click="collapsed = !collapsed"
        >
          <i :class="['pi text-sm', collapsed ? 'pi-angle-right' : 'pi-angle-left']" aria-hidden="true" />
        </button>
      </div>

      <!-- Nav -->
      <nav class="flex-1 overflow-y-auto py-2" aria-label="Sidebar">
        <SidebarLink
          to="/projects"
          icon="pi-folder"
          label="Projects"
          :collapsed="collapsed"
        />
        <template v-if="projectStore.currentProject">
          <div
            v-show="!collapsed"
            class="px-4 py-2 text-xs font-medium text-neutral-100 uppercase tracking-wider"
            aria-hidden="true"
          >
            {{ projectStore.currentProject.name }}
          </div>
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}`"
            icon="pi-th-large"
            label="Dashboard"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/data`"
            icon="pi-database"
            label="Data"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/tuning`"
            icon="pi-sliders-h"
            label="Tuning"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/models`"
            icon="pi-box"
            label="Models"
            :collapsed="collapsed"
          />
        </template>
      </nav>

      <!-- User -->
      <div class="border-t border-neutral-30 p-3">
        <div class="flex items-center">
          <div class="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-white text-sm font-medium" aria-hidden="true">
            {{ authStore.user?.username?.charAt(0)?.toUpperCase() ?? '?' }}
          </div>
          <span
            v-show="!collapsed"
            class="ml-2 text-sm truncate text-neutral-800"
          >{{ authStore.user?.username }}</span>
          <button
            v-show="!collapsed"
            class="ml-auto p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary"
            aria-label="Logout"
            @click="handleLogout"
          >
            <i class="pi pi-sign-out text-sm text-neutral-200" aria-hidden="true" />
          </button>
        </div>
      </div>
    </aside>

    <!-- Main -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <header class="h-14 flex items-center px-6 border-b border-neutral-30 bg-white" role="banner">
        <Breadcrumb :model="breadcrumbItems" />
      </header>
      <main id="main-content" class="flex-1 overflow-y-auto p-6" role="main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import Breadcrumb from "primevue/breadcrumb";
import { useAuthStore } from "@/stores/auth";
import { useProjectStore } from "@/stores/project";
import SidebarLink from "@/components/layout/SidebarLink.vue";

const authStore = useAuthStore();
const projectStore = useProjectStore();
const route = useRoute();
const router = useRouter();
const collapsed = ref(false);

// Build breadcrumb from route
const breadcrumbItems = computed(() => {
  const items: { label: string; to?: string }[] = [];
  if (route.params.projectId) {
    items.push({ label: "Projects", to: "/projects" });
    items.push({ label: projectStore.currentProject?.name ?? "Project" });
    if (route.path.includes("/data")) items.push({ label: "Data" });
    if (route.path.includes("/tuning")) items.push({ label: "Tuning" });
    if (route.path.includes("/models")) items.push({ label: "Models" });
  } else if (route.path.includes("/projects")) {
    items.push({ label: "Projects" });
  }
  return items;
});

// Fetch current project when route changes
watch(() => route.params.projectId, async (id) => {
  if (id) await projectStore.fetchProject(Number(id));
}, { immediate: true });

async function handleLogout() {
  await authStore.logout();
  router.push("/login");
}
</script>
