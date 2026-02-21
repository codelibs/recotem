<template>
  <div class="flex h-screen overflow-hidden bg-neutral-10">
    <Toast position="top-right" />
    <a
      href="#main-content"
      class="skip-link"
    >{{ $t('nav.skipToContent') }}</a>

    <!-- Mobile overlay -->
    <div
      v-if="mobileOpen"
      class="fixed inset-0 z-30 bg-black/40 md:hidden"
      @click="mobileOpen = false"
    />

    <!-- Sidebar -->
    <aside
      :class="[
        'flex flex-col border-r border-neutral-30 bg-neutral-20 transition-all duration-200',
        collapsed ? 'w-14' : 'w-60',
        mobileOpen ? 'fixed inset-y-0 left-0 z-40' : 'hidden md:flex',
      ]"
      role="navigation"
      :aria-label="$t('nav.mainNavigation')"
    >
      <!-- Logo -->
      <div class="flex items-center h-14 px-4 border-b border-neutral-30 shrink-0">
        <img
          src="/favicon.png"
          alt="Recotem"
          class="w-6 h-6 flex-shrink-0"
        >
        <span
          v-show="!collapsed"
          class="ml-3 font-semibold text-neutral-800 truncate"
        >Recotem</span>
        <button
          class="ml-auto p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary hidden md:block"
          :aria-label="collapsed ? $t('nav.expandSidebar') : $t('nav.collapseSidebar')"
          @click="toggleSidebar"
        >
          <i
            :class="['pi text-sm', collapsed ? 'pi-angle-right' : 'pi-angle-left']"
            aria-hidden="true"
          />
        </button>
        <button
          class="ml-auto p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary md:hidden"
          :aria-label="$t('nav.closeMenu')"
          @click="mobileOpen = false"
        >
          <i
            class="pi pi-times text-sm"
            aria-hidden="true"
          />
        </button>
      </div>

      <!-- Nav -->
      <nav
        class="flex-1 overflow-y-auto py-2"
        :aria-label="$t('nav.sidebar')"
      >
        <SidebarLink
          to="/projects"
          icon="pi-folder"
          :label="$t('nav.projects')"
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
            :label="$t('nav.dashboard')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/data`"
            icon="pi-database"
            :label="$t('nav.data')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/tuning`"
            icon="pi-sliders-h"
            :label="$t('nav.tuning')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/models`"
            icon="pi-box"
            :label="$t('nav.models')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/model-configs`"
            icon="pi-cog"
            :label="$t('nav.configs')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/model-comparison`"
            icon="pi-chart-bar"
            :label="$t('nav.compare')"
            :collapsed="collapsed"
          />
          <div
            v-show="!collapsed"
            class="px-4 py-2 text-xs font-medium text-neutral-100 uppercase tracking-wider mt-2"
            aria-hidden="true"
          >
            Production
          </div>
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/api-keys`"
            icon="pi-key"
            :label="$t('nav.apiKeys')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/retraining`"
            icon="pi-clock"
            :label="$t('nav.retraining')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/deployments`"
            icon="pi-server"
            :label="$t('nav.deployments')"
            :collapsed="collapsed"
          />
          <SidebarLink
            :to="`/projects/${projectStore.currentProject.id}/ab-tests`"
            icon="pi-chart-line"
            :label="$t('nav.abTests')"
            :collapsed="collapsed"
          />
        </template>

        <!-- Admin section -->
        <template v-if="authStore.user?.is_staff">
          <div
            v-show="!collapsed"
            class="px-4 py-2 text-xs font-medium text-neutral-100 uppercase tracking-wider mt-2"
            aria-hidden="true"
          >
            {{ $t('nav.admin') }}
          </div>
          <SidebarLink
            to="/users"
            icon="pi-users"
            :label="$t('nav.users')"
            :collapsed="collapsed"
          />
        </template>
      </nav>

      <!-- User -->
      <div class="border-t border-neutral-30 p-3">
        <div class="flex items-center">
          <div
            class="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-white text-sm font-medium"
            aria-hidden="true"
          >
            {{ authStore.user?.username?.charAt(0)?.toUpperCase() ?? '?' }}
          </div>
          <span
            v-show="!collapsed"
            class="ml-2 text-sm truncate text-neutral-800"
          >{{ authStore.user?.username }}</span>
          <button
            v-show="!collapsed"
            class="ml-auto p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary"
            :aria-label="$t('nav.changePassword')"
            @click="router.push('/password')"
          >
            <i
              class="pi pi-lock text-sm text-neutral-200"
              aria-hidden="true"
            />
          </button>
          <button
            v-show="!collapsed"
            class="ml-1 p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary"
            :aria-label="$t('nav.language')"
            @click="toggleLocale"
          >
            <span class="text-xs font-medium text-neutral-200">{{ currentLocale === 'ja' ? 'EN' : 'JA' }}</span>
          </button>
          <button
            v-show="!collapsed"
            class="ml-1 p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary"
            :aria-label="isDark ? $t('nav.switchToLight') : $t('nav.switchToDark')"
            @click="toggleDarkMode"
          >
            <i
              :class="['pi text-sm text-neutral-200', isDark ? 'pi-sun' : 'pi-moon']"
              aria-hidden="true"
            />
          </button>
          <button
            v-show="!collapsed"
            class="ml-1 p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary"
            :aria-label="$t('nav.logout')"
            @click="handleLogout"
          >
            <i
              class="pi pi-sign-out text-sm text-neutral-200"
              aria-hidden="true"
            />
          </button>
        </div>
      </div>
    </aside>

    <!-- Main -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <header
        class="h-14 flex items-center px-6 border-b border-neutral-30 bg-white"
        role="banner"
      >
        <button
          class="mr-3 p-1 rounded hover:bg-neutral-30 focus-visible:outline-2 focus-visible:outline-primary md:hidden"
          :aria-label="$t('nav.openMenu')"
          @click="mobileOpen = true"
        >
          <i
            class="pi pi-bars text-lg"
            aria-hidden="true"
          />
        </button>
        <Breadcrumb :model="breadcrumbItems" />
      </header>
      <main
        id="main-content"
        class="flex-1 overflow-y-auto p-6"
        role="main"
      >
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Breadcrumb from "primevue/breadcrumb";
import Toast from "primevue/toast";
import { useAuthStore } from "@/stores/auth";
import { useProjectStore } from "@/stores/project";
import { useDarkMode } from "@/composables/useDarkMode";
import { setLocale, getLocale } from "@/i18n";
import SidebarLink from "@/components/layout/SidebarLink.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const projectStore = useProjectStore();
const { isDark, toggle: toggleDarkMode } = useDarkMode();
const route = useRoute();
const router = useRouter();
const collapsed = ref(localStorage.getItem("sidebar-collapsed") === "true");
const mobileOpen = ref(false);
const currentLocale = ref(getLocale());

function toggleLocale() {
  const next = currentLocale.value === "en" ? "ja" : "en";
  setLocale(next);
  currentLocale.value = next;
}

function toggleSidebar() {
  collapsed.value = !collapsed.value;
  localStorage.setItem("sidebar-collapsed", String(collapsed.value));
}

// Close mobile sidebar on route change
watch(() => route.path, () => {
  mobileOpen.value = false;
});

// Build breadcrumb from route
const breadcrumbItems = computed(() => {
  const items: { label: string; to?: string }[] = [];
  if (route.params.projectId) {
    items.push({ label: t("nav.projects"), to: "/projects" });
    items.push({ label: projectStore.currentProject?.name ?? "Project" });
    if (route.path.includes("/data")) items.push({ label: t("nav.data") });
    if (route.path.includes("/tuning")) items.push({ label: t("nav.tuning") });
    if (route.path.includes("/models")) items.push({ label: t("nav.models") });
    if (route.path.includes("/api-keys")) items.push({ label: t("nav.apiKeys") });
    if (route.path.includes("/retraining")) items.push({ label: t("nav.retraining") });
    if (route.path.includes("/deployments")) items.push({ label: t("nav.deployments") });
    if (route.path.includes("/ab-tests")) items.push({ label: t("nav.abTests") });
  } else if (route.path.includes("/users")) {
    items.push({ label: t("nav.users") });
  } else if (route.path.includes("/password")) {
    items.push({ label: t("nav.changePassword") });
  } else if (route.path.includes("/projects")) {
    items.push({ label: t("nav.projects") });
  }
  return items;
});

async function handleLogout() {
  await authStore.logout();
  router.push("/login");
}
</script>
