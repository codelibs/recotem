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

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>Failed to load project summary.</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchSummary"
        />
      </div>
    </Message>

    <div
      v-if="loading"
      class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8"
    >
      <Skeleton
        v-for="n in 3"
        :key="n"
        height="6rem"
      />
    </div>

    <div
      v-else
      class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8"
    >
      <StatCard
        icon="pi-database"
        label="Training Data"
        :value="summary?.n_data ?? 0"
        :to="`/projects/${projectId}/data`"
      />
      <StatCard
        icon="pi-sliders-h"
        label="Completed Jobs"
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

    <!-- Data freshness indicator -->
    <div
      v-if="summary && lastFetchedAt"
      class="text-xs text-neutral-200 mb-4"
    >
      Last updated: {{ timeAgo }}
    </div>

    <!-- Pipeline step progress -->
    <div
      v-if="summary && !loading"
      class="bg-white rounded-lg shadow-sm border border-neutral-30 p-4 mb-6"
    >
      <h4 class="text-sm font-semibold text-neutral-500 mb-3">
        Pipeline Progress
      </h4>
      <div class="flex items-center gap-2 text-xs">
        <span :class="stepClass(step1Done)">1. Data</span>
        <i class="pi pi-arrow-right text-neutral-40" />
        <span :class="stepClass(step2Done)">2. Tuning</span>
        <i class="pi pi-arrow-right text-neutral-40" />
        <span :class="stepClass(step3Done)">3. Model</span>
      </div>
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
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import { api } from "@/api/client";
import { useProjectStore } from "@/stores/project";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import StatCard from "@/components/common/StatCard.vue";
import type { ProjectSummary } from "@/types";

const route = useRoute();
const router = useRouter();
const projectStore = useProjectStore();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const summary = ref<ProjectSummary | null>(null);
const loading = ref(false);
const error = ref(false);
const lastFetchedAt = ref<Date | null>(null);

const step1Done = computed(() => (summary.value?.n_data ?? 0) > 0);
const step2Done = computed(() => (summary.value?.n_complete_jobs ?? 0) > 0);
const step3Done = computed(() => (summary.value?.n_models ?? 0) > 0);

const timeAgo = computed(() => {
  if (!lastFetchedAt.value) return "";
  const diff = Math.round((Date.now() - lastFetchedAt.value.getTime()) / 1000);
  if (diff < 60) return "just now";
  const mins = Math.round(diff / 60);
  return `${mins} min ago`;
});

function stepClass(done: boolean) {
  return done
    ? "font-semibold text-success"
    : "text-neutral-200";
}

async function fetchSummary() {
  loading.value = true;
  error.value = false;
  try {
    summary.value = await api(`/project_summary/${projectId}/`, { signal });
    lastFetchedAt.value = new Date();
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = true;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(fetchSummary);
</script>
