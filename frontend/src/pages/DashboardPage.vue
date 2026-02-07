<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-neutral-800">
        {{ projectStore.currentProject?.name ?? "Dashboard" }}
      </h1>
      <Button
        label="Start Tuning"
        icon="pi pi-play"
        @click="router.push(`/projects/${projectId}/tuning/new`)"
      />
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-4">
      Failed to load project summary. Please try again.
    </Message>

    <div v-if="loading" class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      <Skeleton height="6rem" v-for="n in 3" :key="n" />
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      <StatCard
        icon="pi-database"
        label="Training Data"
        :value="summary?.n_data ?? 0"
        :to="`/projects/${projectId}/data`"
      />
      <StatCard
        icon="pi-sliders-h"
        label="Tuning Jobs"
        :value="summary?.n_complete_jobs ?? 0"
        :to="`/projects/${projectId}/tuning`"
      />
      <StatCard
        icon="pi-box"
        label="Trained Models"
        :value="summary?.n_models ?? 0"
        :to="`/projects/${projectId}/models`"
      />
    </div>

    <div
      v-if="!summary && !loading && !error"
      class="text-center py-12"
    >
      <i class="pi pi-info-circle text-4xl text-neutral-40 mb-3" />
      <h3 class="text-lg font-medium text-neutral-500">
        Get started
      </h3>
      <p class="text-neutral-200 mt-1 mb-4">
        Upload training data, then start tuning.
      </p>
      <Button
        label="Upload Data"
        icon="pi pi-upload"
        @click="router.push(`/projects/${projectId}/data/upload`)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import { api } from "@/api/client";
import { useProjectStore } from "@/stores/project";
import StatCard from "@/components/common/StatCard.vue";
import type { ProjectSummary } from "@/types";

const route = useRoute();
const router = useRouter();
const projectStore = useProjectStore();
const projectId = route.params.projectId as string;
const summary = ref<ProjectSummary | null>(null);
const loading = ref(false);
const error = ref(false);

onMounted(async () => {
  loading.value = true;
  error.value = false;
  try {
    summary.value = await api(`/project_summary/${projectId}/`);
  } catch {
    error.value = true;
  } finally {
    loading.value = false;
  }
});
</script>
