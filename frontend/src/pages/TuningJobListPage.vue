<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        {{ $t('tuning.title') }}
      </h2>
      <Button
        :label="$t('tuning.newJob')"
        icon="pi pi-plus"
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
        <span>{{ error?.message ?? $t('tuning.failedToLoad') }}</span>
        <Button
          :label="$t('common.retry')"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchJobs"
        />
      </div>
    </Message>

    <div
      v-if="loading"
      class="space-y-3"
    >
      <Skeleton
        v-for="i in 5"
        :key="i"
        height="3rem"
      />
    </div>

    <EmptyState
      v-else-if="!error && jobs.length === 0"
      icon="pi-sliders-h"
      :title="$t('tuning.noJobs')"
      :description="$t('tuning.startFirst')"
    >
      <Button
        :label="$t('tuning.newJob')"
        icon="pi pi-plus"
        @click="router.push(`/projects/${projectId}/tuning/new`)"
      />
    </EmptyState>

    <div
      v-else
      class="overflow-x-auto"
    >
      <DataTable
        :value="jobs"
        striped-rows
        paginator
        :rows="20"
      >
        <Column
          field="id"
          header="ID"
          sortable
          :style="{ width: '80px' }"
        />
        <Column :header="$t('common.status')">
          <template #body="{ data }">
            <Tag
              :severity="statusSeverity(data)"
              :value="statusLabel(data)"
            />
          </template>
        </Column>
        <Column :header="$t('tuning.bestScore')">
          <template #body="{ data }">
            {{ data.best_score != null ? data.best_score.toFixed(4) : '-' }}
          </template>
        </Column>
        <Column
          field="n_trials"
          :header="$t('tuning.trials')"
          sortable
        />
        <Column
          field="ins_datetime"
          :header="$t('common.createdAt')"
          sortable
        >
          <template #body="{ data }">
            {{ formatDate(data.ins_datetime) }}
          </template>
        </Column>
        <Column
          :header="$t('common.actions')"
          :style="{ width: '100px' }"
        >
          <template #body="{ data }">
            <Button
              icon="pi pi-eye"
              text
              rounded
              :aria-label="t('tuning.viewJob', { id: data.id })"
              @click="router.push(`/projects/${projectId}/tuning/${data.id}`)"
            />
          </template>
        </Column>
      </DataTable>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import type { ClassifiedApiError, ParameterTuningJob } from "@/types";
import { JOB_STATUS } from "@/types";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError, unwrapResults } from "@/api/client";
import EmptyState from "@/components/common/EmptyState.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const jobs = ref<ParameterTuningJob[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);

function statusLabel(job: ParameterTuningJob): string {
  if (job.status) return t(`tuning.jobStatus.${job.status}`);
  return job.best_config ? t('tuning.jobStatus.COMPLETED') : t('tuning.jobStatus.PENDING');
}

function statusSeverity(job: ParameterTuningJob): string {
  const status = job.status ?? (job.best_config ? JOB_STATUS.COMPLETED : JOB_STATUS.PENDING);
  switch (status) {
    case JOB_STATUS.COMPLETED: return "success";
    case JOB_STATUS.FAILED: return "danger";
    case JOB_STATUS.RUNNING: return "info";
    default: return "secondary";
  }
}

async function fetchJobs() {
  loading.value = true;
  error.value = null;
  try {
    const res = await api(ENDPOINTS.PARAMETER_TUNING_JOB, { params: { data__project: projectId }, signal });
    jobs.value = unwrapResults(res);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

// Auto-poll when there are active (PENDING/RUNNING) jobs
const hasActiveJobs = computed(() =>
  jobs.value.some(j => j.status === "PENDING" || j.status === "RUNNING"),
);

let pollTimer: ReturnType<typeof setInterval> | null = null;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (hasActiveJobs.value) {
      try {
        const res = await api(ENDPOINTS.PARAMETER_TUNING_JOB, { params: { data__project: projectId }, signal });
        jobs.value = unwrapResults(res);
      } catch { /* ignore poll errors */ }
    }
  }, 15000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onMounted(() => {
  fetchJobs().then(startPolling);
});

onUnmounted(stopPolling);
</script>
