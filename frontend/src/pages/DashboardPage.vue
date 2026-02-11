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

    <!-- Production overview -->
    <div
      v-if="summary && !loading"
      class="bg-white rounded-lg shadow-sm border border-neutral-30 p-4 mb-6"
    >
      <h4 class="text-sm font-semibold text-neutral-500 mb-3">
        Production
      </h4>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <!-- Retraining Schedule -->
        <router-link
          :to="`/projects/${projectId}/retraining`"
          class="flex items-center gap-3 p-3 rounded-lg hover:bg-neutral-20 transition-colors"
        >
          <div class="w-8 h-8 rounded-md bg-blue-50 flex items-center justify-center">
            <i class="pi pi-clock text-blue-600" />
          </div>
          <div>
            <div class="text-sm font-medium text-neutral-800">
              {{ retrainingLabel }}
            </div>
            <div class="text-xs text-neutral-400">
              Retraining
            </div>
          </div>
        </router-link>

        <!-- Deployment Slots -->
        <router-link
          :to="`/projects/${projectId}/deployments`"
          class="flex items-center gap-3 p-3 rounded-lg hover:bg-neutral-20 transition-colors"
        >
          <div class="w-8 h-8 rounded-md bg-green-50 flex items-center justify-center">
            <i class="pi pi-server text-green-600" />
          </div>
          <div>
            <div class="text-sm font-medium text-neutral-800">
              {{ activeSlotCount }} active slot{{ activeSlotCount !== 1 ? 's' : '' }}
            </div>
            <div class="text-xs text-neutral-400">
              Deployments
            </div>
          </div>
        </router-link>

        <!-- A/B Tests -->
        <router-link
          :to="`/projects/${projectId}/ab-tests`"
          class="flex items-center gap-3 p-3 rounded-lg hover:bg-neutral-20 transition-colors"
        >
          <div class="w-8 h-8 rounded-md bg-amber-50 flex items-center justify-center">
            <i class="pi pi-chart-line text-amber-600" />
          </div>
          <div>
            <div class="text-sm font-medium text-neutral-800">
              {{ runningTestCount }} running test{{ runningTestCount !== 1 ? 's' : '' }}
            </div>
            <div class="text-xs text-neutral-400">
              A/B Tests
            </div>
          </div>
        </router-link>
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
import type { RetrainingSchedule, DeploymentSlot, ABTest } from "@/types/production";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError, unwrapResults } from "@/api/client";
import { getRetrainingSchedules, getDeploymentSlots, getABTests } from "@/api/production";

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

// Production overview
const retrainingSchedule = ref<RetrainingSchedule | null>(null);
const activeSlotCount = ref(0);
const runningTestCount = ref(0);

const retrainingLabel = computed(() => {
  if (!retrainingSchedule.value) return "Not configured";
  if (!retrainingSchedule.value.is_enabled) return "Disabled";
  if (retrainingSchedule.value.next_run_at) {
    return `Next: ${new Date(retrainingSchedule.value.next_run_at).toLocaleDateString()}`;
  }
  return "Enabled";
});

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

async function fetchProductionOverview() {
  const pid = Number(projectId);
  try {
    const [schedRes, slotRes, testRes] = await Promise.allSettled([
      getRetrainingSchedules(pid, signal),
      getDeploymentSlots(pid, signal),
      getABTests(pid, signal),
    ]);
    if (schedRes.status === "fulfilled") {
      const schedules = unwrapResults(schedRes.value);
      retrainingSchedule.value = schedules.length > 0 ? schedules[0] : null;
    }
    if (slotRes.status === "fulfilled") {
      activeSlotCount.value = unwrapResults(slotRes.value).filter(s => s.is_active).length;
    }
    if (testRes.status === "fulfilled") {
      runningTestCount.value = unwrapResults(testRes.value).filter(t => t.status === "RUNNING").length;
    }
  } catch {
    // Non-critical — production overview is best-effort
  }
}

let refreshTimer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  fetchSummary();
  fetchProductionOverview();
  refreshTimer = setInterval(fetchSummary, 30000);
});

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer);
});
</script>
