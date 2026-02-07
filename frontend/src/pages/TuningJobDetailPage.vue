<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        aria-label="Back to tuning jobs"
        @click="router.push(`/projects/${projectId}/tuning`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        Tuning Job #{{ jobId }}
      </h2>
      <Tag
        v-if="job"
        :severity="statusSeverity"
        :value="job.status"
      />
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>Failed to load tuning job details.</span>
        <Button
          label="Retry"
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
    >
      <div class="flex items-center gap-2">
        <i class="pi pi-spin pi-spinner" />
        <span>Real-time updates paused. Reconnecting...</span>
      </div>
    </Message>

    <Message
      v-if="wsConnectionState === 'disconnected' && wsWasConnected"
      severity="info"
      :closable="false"
      class="mb-4"
    >
      <span>Real-time updates disconnected. Refresh to reconnect.</span>
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
          Job Details
        </h3>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div><span class="text-neutral-100">Trials:</span> {{ job.n_trials }}</div>
          <div><span class="text-neutral-100">Parallel:</span> {{ job.n_tasks_parallel }}</div>
          <div><span class="text-neutral-100">Memory:</span> {{ job.memory_budget }} MB</div>
          <div><span class="text-neutral-100">Best Score:</span> {{ job.best_score?.toFixed(4) ?? '-' }}</div>
          <div><span class="text-neutral-100">Created:</span> {{ formatDate(job.ins_datetime) }}</div>
          <div><span class="text-neutral-100">Train After:</span> {{ job.train_after_tuning ? 'Yes' : 'No' }}</div>
        </div>

        <div
          v-if="job.status === 'COMPLETED' && job.best_config && !job.tuned_model"
          class="mt-4"
        >
          <Button
            label="Train Model"
            icon="pi pi-play"
            @click="router.push(`/projects/${projectId}/models`)"
          />
        </div>
      </div>

      <!-- Logs -->
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-neutral-800">
            Logs
          </h3>
          <div class="flex items-center gap-2">
            <Button
              v-if="allLogs.length > 0"
              v-tooltip="'Download logs'"
              icon="pi pi-download"
              text
              size="small"
              aria-label="Download logs"
              @click="downloadLogs"
            />
            <Tag
              v-if="wsConnected"
              severity="success"
              value="Live"
            />
          </div>
        </div>
        <div
          ref="logContainer"
          class="bg-neutral-10 rounded-md p-4 max-h-96 overflow-y-auto font-mono text-xs space-y-1"
          aria-live="polite"
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
            No logs yet...
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import Tooltip from "primevue/tooltip";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { useJobLogs } from "@/composables/useJobStatus";
import type { ParameterTuningJob, TaskLog } from "@/types";

const vTooltip = Tooltip;

const route = useRoute();
const router = useRouter();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const jobId = route.params.jobId as string;
const job = ref<ParameterTuningJob | null>(null);
const taskLogs = ref<TaskLog[]>([]);
const logContainer = ref<HTMLElement | null>(null);
const loading = ref(false);
const error = ref(false);
const wsWasConnected = ref(false);

const { logs: wsLogs, isConnected: wsConnected, connectionState: wsConnectionState, connect } = useJobLogs(Number(jobId));

watch(wsConnected, (val) => {
  if (val) wsWasConnected.value = true;
});

const statusSeverity = computed(() => {
  switch (job.value?.status) {
    case "COMPLETED": return "success";
    case "FAILED": return "danger";
    case "RUNNING": return "info";
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
  error.value = false;
  try {
    job.value = await api(`/parameter_tuning_job/${jobId}/`, { signal });
    try {
      const res = await api(`/task_log/`, { params: { tuning_job_id: jobId }, signal });
      taskLogs.value = res.results ?? res;
    } catch { /* no logs endpoint or no logs */ }
    if (!job.value?.best_config) connect();
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = true;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(loadJob);
</script>
