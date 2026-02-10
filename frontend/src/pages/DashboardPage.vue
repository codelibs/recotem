<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-neutral-800">
        {{ currentProject?.name ?? t('dashboard.title') }}
      </h1>
      <Button
        :label="t('dashboard.startTuning')"
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
        <span>{{ error?.message ?? t('dashboard.failedToLoadSummary') }}</span>
        <Button
          :label="t('common.retry')"
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
        :label="t('dashboard.trainingData')"
        :value="summary?.n_data ?? 0"
        :to="`/projects/${projectId}/data`"
      />
      <StatCard
        icon="pi-sliders-h"
        :label="t('dashboard.completedJobs')"
        :value="summary?.n_complete_jobs ?? 0"
        :to="`/projects/${projectId}/tuning`"
      />
      <StatCard
        icon="pi-box"
        :label="t('dashboard.trainedModels')"
        :value="summary?.n_models ?? 0"
        :to="`/projects/${projectId}/models`"
      />
    </div>

    <!-- Data freshness indicator -->
    <div
      v-if="summary && lastFetchedAt"
      class="text-xs text-neutral-200 mb-4"
    >
      {{ t('common.lastUpdated') }}: {{ timeAgo }}
    </div>

    <!-- Pipeline step progress -->
    <div
      v-if="summary && !loading"
      class="bg-white rounded-lg shadow-sm border border-neutral-30 p-4 mb-6"
    >
      <h4 class="text-sm font-semibold text-neutral-500 mb-3">
        {{ t('dashboard.pipelineProgress') }}
      </h4>
      <div class="flex items-center gap-2 text-xs">
        <span :class="stepClass(step1Done)">{{ t('dashboard.stepData') }}</span>
        <i class="pi pi-arrow-right text-neutral-40" />
        <span :class="stepClass(step2Done)">{{ t('dashboard.stepTuning') }}</span>
        <i class="pi pi-arrow-right text-neutral-40" />
        <span :class="stepClass(step3Done)">{{ t('dashboard.stepModel') }}</span>
      </div>
    </div>

    <EmptyState
      v-if="!summary && !loading && !error"
      icon="pi-info-circle"
      :title="t('dashboard.getStarted')"
      :description="t('dashboard.uploadToStart')"
    >
      <Button
        :label="t('dashboard.uploadData')"
        icon="pi pi-upload"
        @click="router.push(`/projects/${projectId}/data/upload`)"
      />
    </EmptyState>
  </div>
</template>

<script setup lang="ts">
import { type ComputedRef, ref, computed, inject, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import { api } from "@/api/client";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import EmptyState from "@/components/common/EmptyState.vue";
import StatCard from "@/components/common/StatCard.vue";
import type { ClassifiedApiError, Project, ProjectSummary } from "@/types";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const currentProject = inject<ComputedRef<Project | null>>("currentProject");
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const summary = ref<ProjectSummary | null>(null);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);
const lastFetchedAt = ref<Date | null>(null);

const step1Done = computed(() => (summary.value?.n_data ?? 0) > 0);
const step2Done = computed(() => (summary.value?.n_complete_jobs ?? 0) > 0);
const step3Done = computed(() => (summary.value?.n_models ?? 0) > 0);

const timeAgo = computed(() => {
  if (!lastFetchedAt.value) return "";
  const diff = Math.round((Date.now() - lastFetchedAt.value.getTime()) / 1000);
  if (diff < 60) return t('common.justNow');
  const mins = Math.round(diff / 60);
  return t('common.minAgo', { n: mins });
});

function stepClass(done: boolean) {
  return done
    ? "font-semibold text-success"
    : "text-neutral-200";
}

async function fetchSummary() {
  loading.value = true;
  error.value = null;
  try {
    summary.value = await api(ENDPOINTS.PROJECT_SUMMARY(Number(projectId)), { signal });
    lastFetchedAt.value = new Date();
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

let refreshTimer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  fetchSummary();
  refreshTimer = setInterval(fetchSummary, 30000);
});

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer);
});
</script>
