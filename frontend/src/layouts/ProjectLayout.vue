<template>
  <div
    v-if="projectStore.loading"
    class="p-6"
  >
    <Skeleton
      width="40%"
      height="2rem"
      class="mb-4"
    />
    <Skeleton
      width="100%"
      height="12rem"
    />
  </div>

  <Message
    v-else-if="projectStore.error"
    severity="error"
    :closable="false"
    class="m-6"
  >
    <div class="flex items-center gap-2">
      <span>{{ projectStore.error.message ?? t('projects.failedToLoadProject') }}</span>
      <Button
        :label="t('common.retry')"
        icon="pi pi-refresh"
        text
        size="small"
        @click="loadProject"
      />
      <Button
        :label="t('projects.backToProjects')"
        icon="pi pi-arrow-left"
        text
        size="small"
        @click="router.push('/projects')"
      />
    </div>
  </Message>

  <router-view v-else />
</template>

<script setup lang="ts">
import { computed, provide, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import { useProjectStore } from "@/stores/project";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectStore = useProjectStore();

function loadProject() {
  const id = Number(route.params.projectId);
  if (id) {
    projectStore.fetchProject(id);
  }
}

watch(
  () => route.params.projectId,
  (newId) => {
    if (newId) {
      projectStore.fetchProject(Number(newId));
    }
  },
  { immediate: true },
);

provide("currentProject", computed(() => projectStore.currentProject));
</script>
