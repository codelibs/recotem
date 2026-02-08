<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        :aria-label="$t('tuning.backToJobs')"
        @click="router.push(`/projects/${projectId}/tuning`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        {{ $t('tuning.jobDetail') }} #{{ jobId }}
      </h2>
      <Tag
        v-if="job"
        :severity="statusSeverity"
        :value="job.status ? $t(`tuning.jobStatus.${job.status}`) : ''"
        aria-live="polite"
      />
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>{{ error?.message ?? $t('tuning.failedToLoadDetail') }}</span>
        <Button
          :label="$t('common.retry')"
          icon="pi pi-refresh"
          text
          size="small"
          @click="loadJob"
        />
      </div>
    </Message>

    <Message
      v-if="wsConnectionState === 'reconnecting'"
      severity="warn"
      :closable="false"
      class="mb-4"
      aria-live="assertive"
    >
      <div class="flex items-center gap-2">
        <i class="pi pi-spin pi-spinner" />
        <span>{{ $t('tuning.reconnectingMessage') }}</span>
      </div>
    </Message>

    <Message
      v-if="wsConnectionState === 'disconnected' && wsWasConnected"
      severity="info"
      :closable="false"
      class="mb-4"
      aria-live="assertive"
    >
      <span>{{ $t('tuning.disconnectedMessage') }}</span>
    </Message>

    <div
      v-if="loading"
      class="space-y-4"
    >
      <Skeleton height="10rem" />
      <Skeleton height="12rem" />
    </div>

    <div
      v-else-if="job"
      class="space-y-6"
    >
      <!-- Job Info -->
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <h3 class="font-semibold text-neutral-800 mb-4">
          {{ $t('tuning.jobDetails') }}
        </h3>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div><span class="text-neutral-100">{{ $t('tuning.trials') }}:</span> {{ job.n_trials }}</div>
          <div><span class="text-neutral-100">{{ $t('tuning.parallel') }}:</span> {{ job.n_tasks_parallel }}</div>
          <div><span class="text-neutral-100">{{ $t('tuning.memory') }}:</span> {{ job.memory_budget }} MB</div>
          <div><span class="text-neutral-100">{{ $t('tuning.bestScore') }}:</span> {{ job.best_score?.toFixed(4) ?? '-' }}</div>
          <div><span class="text-neutral-100">{{ $t('common.createdAt') }}:</span> {{ formatDate(job.ins_datetime) }}</div>
          <div><span class="text-neutral-100">{{ $t('tuning.trainAfter') }}:</span> {{ job.train_after_tuning ? $t('tuning.yes') : $t('tuning.no') }}</div>
        </div>

        <div
          v-if="job.status === 'COMPLETED' && job.best_config && !job.tuned_model"
          class="mt-4"
        >
          <Button
            :label="$t('models.trainModel')"
            icon="pi pi-play"
            @click="router.push(`/projects/${projectId}/models`)"
          />
        </div>
      </div>

      <!-- Logs -->
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-neutral-800">
            {{ $t('tuning.logs') }}
          </h3>
          <div class="flex items-center gap-2">
            <Button
              v-if="allLogs.length > 0"
              v-tooltip="$t('tuning.downloadLogs')"
              icon="pi pi-download"
              text
              size="small"
              :aria-label="$t('tuning.downloadLogs')"
              @click="downloadLogs"
            />
            <Tag
              v-if="wsConnected"
              severity="success"
              :value="$t('tuning.live')"
            />
          </div>
        </div>
        <div
          ref="logContainer"
          aria-live="polite"
          :aria-label="$t('tuning.jobLogsLabel')"
          class="bg-neutral-10 rounded-md p-4 max-h-96 overflow-y-auto font-mono text-xs space-y-1"
        >
          <div
            v-for="(log, i) in allLogs"
            :key="i"
            class="text-neutral-500"
          >
            {{ log }}
          </div>
          <div
            v-if="allLogs.length === 0"
            class="text-neutral-100"
          >
            {{ $t('tuning.noLogsYet') }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import Tooltip from "primevue/tooltip";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { useJobLogs } from "@/composables/useJobStatus";
import type { ClassifiedApiError, ParameterTuningJob, TaskLog } from "@/types";
import { JOB_STATUS } from "@/types";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError, unwrapResults } from "@/api/client";

const vTooltip = Tooltip;

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const jobId = route.params.jobId as string;
const job = ref<ParameterTuningJob | null>(null);
const taskLogs = ref<TaskLog[]>([]);
const logContainer = ref<HTMLElement | null>(null);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);
const wsWasConnected = ref(false);

const { logs: wsLogs, isConnected: wsConnected, connectionState: wsConnectionState, connect } = useJobLogs(Number(jobId));

watch(wsConnected, (val) => {
  if (val) wsWasConnected.value = true;
});

const statusSeverity = computed(() => {
  switch (job.value?.status) {
    case JOB_STATUS.COMPLETED: return "success";
    case JOB_STATUS.FAILED: return "danger";
    case JOB_STATUS.RUNNING: return "info";
    default: return "secondary";
  }
});

const allLogs = computed(() => {
  const historic = taskLogs.value.map(l => l.contents);
  const live = wsLogs.value.map((l: any) => l.message ?? JSON.stringify(l));
  return [...historic, ...live];
});

function downloadLogs() {
  const text = allLogs.value.join("\n");
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `tuning-job-${jobId}-logs.txt`;
  a.click();
  URL.revokeObjectURL(url);
}

watch(allLogs, async () => {
  await nextTick();
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight;
  }
});

async function loadJob() {
  loading.value = true;
  error.value = null;
  try {
    job.value = await api(ENDPOINTS.PARAMETER_TUNING_JOB_DETAIL(Number(jobId)), { signal });
    try {
      const res = await api(ENDPOINTS.TASK_LOG, { params: { tuning_job_id: jobId }, signal });
      taskLogs.value = unwrapResults(res);
    } catch { /* no logs endpoint or no logs */ }
    if (!job.value?.best_config) connect();
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

onMounted(loadJob);
</script>
