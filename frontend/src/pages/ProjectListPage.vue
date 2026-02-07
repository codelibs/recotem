<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-neutral-800">
        Projects
      </h1>
      <Button
        label="New Project"
        icon="pi pi-plus"
        @click="showCreate = true"
      />
    </div>

    <Message v-if="projectStore.error" severity="error" :closable="false" class="mb-4">
      Failed to load projects. Please try again.
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
      v-else-if="projectStore.projects.length === 0"
      class="text-center py-16"
    >
      <i class="pi pi-folder-open text-5xl text-neutral-40 mb-4" />
      <h3 class="text-lg font-medium text-neutral-500">
        No projects yet
      </h3>
      <p class="text-neutral-200 mt-1 mb-4">
        Create your first project to get started
      </p>
      <Button
        label="Create Project"
        icon="pi pi-plus"
        @click="showCreate = true"
      />
    </div>

    <div
      v-else
      class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
    >
      <div
        v-for="project in projectStore.projects"
        :key="project.id"
        class="bg-white rounded-lg shadow-sm p-5 cursor-pointer hover:shadow-md transition-shadow border border-neutral-30"
        @click="router.push(`/projects/${project.id}`)"
      >
        <h3 class="font-semibold text-neutral-800 mb-2">
          {{ project.name }}
        </h3>
        <div class="text-sm text-neutral-200 space-y-1">
          <div><span class="text-neutral-100">User:</span> {{ project.user_column }}</div>
          <div><span class="text-neutral-100">Item:</span> {{ project.item_column }}</div>
          <div v-if="project.time_column">
            <span class="text-neutral-100">Time:</span> {{ project.time_column }}
          </div>
        </div>
        <div class="mt-3 text-xs text-neutral-100">
          {{ formatDate(project.ins_datetime) }}
        </div>
      </div>
    </div>

    <Dialog
      v-model:visible="showCreate"
      header="Create Project"
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
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import Button from "primevue/button";
import Dialog from "primevue/dialog";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import dayjs from "dayjs";
import { useProjectStore } from "@/stores/project";
import ProjectCreateForm from "@/components/project/ProjectCreateForm.vue";

const projectStore = useProjectStore();
const router = useRouter();
const showCreate = ref(false);

onMounted(() => projectStore.fetchProjects());

function formatDate(dt: string) {
  return dayjs(dt).format("MMM D, YYYY");
}

function onProjectCreated() {
  showCreate.value = false;
}
</script>
