<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-neutral-800">
        {{ t('projects.title') }}
      </h1>
      <Button
        :label="t('projects.newProject')"
        icon="pi pi-plus"
        @click="showCreate = true"
      />
    </div>

    <Message
      v-if="projectStore.error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>{{ t('projects.failedToLoad') }}</span>
        <Button
          :label="t('common.retry')"
          icon="pi pi-refresh"
          text
          size="small"
          @click="projectStore.fetchProjects()"
        />
      </div>
    </Message>

    <div
      v-if="projectStore.loading"
      class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
    >
      <Skeleton
        v-for="i in 6"
        :key="i"
        height="140px"
        border-radius="var(--radius-lg)"
      />
    </div>

    <div
      v-if="!projectStore.loading && !projectStore.error && projectStore.projects.length > 0"
      class="mb-4"
    >
      <InputText
        v-model="searchQuery"
        :placeholder="t('common.search')"
        class="w-full md:w-80"
      />
    </div>

    <div
      v-if="!projectStore.loading && !projectStore.error && projectStore.projects.length > 0 && filteredProjects.length === 0"
      class="text-center py-12 text-neutral-200"
    >
      {{ t('common.noResults') }}
    </div>

    <EmptyState
      v-else-if="!projectStore.loading && projectStore.projects.length === 0"
      icon="pi-folder-open"
      :title="t('projects.noProjectsYet')"
      :description="t('projects.createFirstProject')"
    >
      <Button
        :label="t('projects.createProject')"
        icon="pi pi-plus"
        @click="showCreate = true"
      />
    </EmptyState>

    <div
      v-else
      class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
    >
      <div
        v-for="project in filteredProjects"
        :key="project.id"
        class="bg-white rounded-lg shadow-sm p-5 cursor-pointer hover:shadow-md transition-shadow border border-neutral-30 relative"
        :class="{ 'opacity-50 pointer-events-none': deletingId === project.id }"
        @click="router.push(`/projects/${project.id}`)"
      >
        <button
          v-if="isOwner(project)"
          class="kebab-menu-btn absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full hover:bg-neutral-100/20 text-neutral-200 hover:text-neutral-800 transition-colors"
          :aria-label="t('projects.deleteProject')"
          @click.stop="openMenu($event, project)"
        >
          <i class="pi pi-ellipsis-v" />
        </button>
        <h3 class="font-semibold text-neutral-800 mb-2 pr-8">
          {{ project.name }}
        </h3>
        <div class="text-sm text-neutral-200 space-y-1">
          <div><span class="text-neutral-100">{{ t('projects.userColumn') }}:</span> {{ project.user_column }}</div>
          <div><span class="text-neutral-100">{{ t('projects.itemColumn') }}:</span> {{ project.item_column }}</div>
          <div v-if="project.time_column">
            <span class="text-neutral-100">{{ t('projects.timeColumn') }}:</span> {{ project.time_column }}
          </div>
        </div>
        <div class="mt-3 text-xs text-neutral-100">
          {{ formatDate(project.ins_datetime) }}
        </div>
      </div>
    </div>

    <Menu
      ref="menuRef"
      :model="menuItems"
      :popup="true"
    />

    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      :header="t('projects.deleteProject')"
      :message="t('projects.deleteConfirmDetail')"
      :confirm-label="t('common.delete')"
      :cancel-label="t('common.cancel')"
      danger
      @confirm="confirmDelete"
    />

    <Dialog
      v-model:visible="showCreate"
      :header="t('projects.createProject')"
      modal
      :style="{ width: '450px' }"
    >
      <ProjectCreateForm
        @created="onProjectCreated"
        @cancel="showCreate = false"
      />
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Button from "primevue/button";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import Menu from "primevue/menu";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import { useProjectStore } from "@/stores/project";
import { useAuthStore } from "@/stores/auth";
import { useNotification } from "@/composables/useNotification";
import { formatDate } from "@/utils/format";
import EmptyState from "@/components/common/EmptyState.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import ProjectCreateForm from "@/components/project/ProjectCreateForm.vue";
import type { Project } from "@/types";

const { t } = useI18n();
const projectStore = useProjectStore();
const authStore = useAuthStore();
const router = useRouter();
const notification = useNotification();
const showCreate = ref(false);
const searchQuery = ref("");
const menuRef = ref();
const showDeleteConfirm = ref(false);
const deleteTarget = ref<Project | null>(null);
const deletingId = ref<number | null>(null);

const filteredProjects = computed(() => {
  const q = searchQuery.value.toLowerCase().trim();
  if (!q) return projectStore.projects;
  return projectStore.projects.filter(p => p.name.toLowerCase().includes(q));
});

const menuItems = computed(() => [
  {
    label: t("projects.deleteProject"),
    icon: "pi pi-trash",
    class: "text-red-600",
    command: () => {
      showDeleteConfirm.value = true;
    },
  },
]);

function isOwner(project: Project): boolean {
  return project.owner === authStore.user?.pk;
}

function openMenu(event: Event, project: Project) {
  deleteTarget.value = project;
  menuRef.value?.toggle(event);
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  const project = deleteTarget.value;
  deletingId.value = project.id;
  try {
    await projectStore.deleteProject(project.id);
    notification.success(t("projects.deleteSuccess"));
  } catch {
    notification.error(t("projects.deleteFailed"));
  } finally {
    deletingId.value = null;
    deleteTarget.value = null;
  }
}

onMounted(() => projectStore.fetchProjects());

function onProjectCreated() {
  showCreate.value = false;
}
</script>
