<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        @click="router.push(`/projects/${projectId}/tuning`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        Tuning Job #{{ jobId }}
      </h2>
      <Tag
        v-if="job"
        :severity="job.best_config ? 'success' : 'info'"
        :value="job.best_config ? 'Complete' : 'Running'"
      />
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-4">
      Failed to load tuning job details. Please try again.
    </Message>

    <div v-if="loading" class="space-y-4">
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
      </div>

      <!-- Logs -->
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-neutral-800">
            Logs
          </h3>
          <Tag
            v-if="wsConnected"
            severity="success"
            value="Live"
          />
        </div>
        <div
          ref="logContainer"
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
import dayjs from "dayjs";
import { api } from "@/api/client";
import { useJobLogs } from "@/composables/useJobStatus";
import type { ParameterTuningJob, TaskLog } from "@/types";

const route = useRoute();
const router = useRouter();
const projectId = route.params.projectId as string;
const jobId = route.params.jobId as string;
const job = ref<ParameterTuningJob | null>(null);
const taskLogs = ref<TaskLog[]>([]);
const logContainer = ref<HTMLElement | null>(null);
const loading = ref(false);
const error = ref(false);

const { logs: wsLogs, isConnected: wsConnected, connect } = useJobLogs(Number(jobId));

const allLogs = computed(() => {
  const historic = taskLogs.value.map(l => l.contents);
  const live = wsLogs.value.map((l: any) => l.message || l.contents || JSON.stringify(l));
  return [...historic, ...live];
});

watch(allLogs, async () => {
  await nextTick();
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight;
  }
});

onMounted(async () => {
  loading.value = true;
  error.value = false;
  try {
    job.value = await api(`/parameter_tuning_job/${jobId}/`);
    try {
      const res = await api(`/task_log/`, { params: { tuning_job_id: jobId } });
      taskLogs.value = res.results ?? res;
    } catch { /* no logs endpoint or no logs */ }
    if (!job.value?.best_config) connect();
  } catch {
    error.value = true;
  } finally {
    loading.value = false;
  }
});

function formatDate(dt: string) {
  return dayjs(dt).format("MMM D, YYYY HH:mm");
}
</script>
