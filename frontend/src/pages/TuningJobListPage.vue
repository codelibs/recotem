<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        Tuning Jobs
      </h2>
      <Button
        label="New Tuning"
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
        <span>Failed to load tuning jobs.</span>
        <Button
          label="Retry"
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

    <div
      v-else-if="!error && jobs.length === 0"
      class="text-center py-16"
    >
      <i class="pi pi-sliders-h text-5xl text-neutral-40 mb-4" />
      <h3 class="text-lg font-medium text-neutral-500">
        No tuning jobs yet
      </h3>
      <p class="text-neutral-200 mt-1 mb-4">
        Start a tuning job to find the best model parameters
      </p>
      <Button
        label="New Tuning"
        icon="pi pi-plus"
        @click="router.push(`/projects/${projectId}/tuning/new`)"
      />
    </div>

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
        <Column header="Status">
          <template #body="{ data }">
            <Tag
              :severity="statusSeverity(data)"
              :value="statusLabel(data)"
            />
          </template>
        </Column>
        <Column header="Best Score">
          <template #body="{ data }">
            {{ data.best_score != null ? data.best_score.toFixed(4) : '-' }}
          </template>
        </Column>
        <Column
          field="n_trials"
          header="Trials"
          sortable
        />
        <Column
          field="ins_datetime"
          header="Created"
          sortable
        >
          <template #body="{ data }">
            {{ formatDate(data.ins_datetime) }}
          </template>
        </Column>
        <Column
          header="Actions"
          :style="{ width: '100px' }"
        >
          <template #body="{ data }">
            <Button
              icon="pi pi-eye"
              text
              rounded
              :aria-label="`View tuning job #${data.id}`"
              @click="router.push(`/projects/${projectId}/tuning/${data.id}`)"
            />
          </template>
        </Column>
      </DataTable>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import type { ParameterTuningJob } from "@/types";

const route = useRoute();
const router = useRouter();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const jobs = ref<ParameterTuningJob[]>([]);
const loading = ref(false);
const error = ref(false);

function statusLabel(job: ParameterTuningJob): string {
  if (job.status) return job.status;
  return job.best_config ? "Complete" : "Pending";
}

function statusSeverity(job: ParameterTuningJob): string {
  const status = job.status ?? (job.best_config ? "COMPLETED" : "PENDING");
  switch (status) {
    case "COMPLETED": return "success";
    case "FAILED": return "danger";
    case "RUNNING": return "info";
    default: return "secondary";
  }
}

async function fetchJobs() {
  loading.value = true;
  error.value = false;
  try {
    const res = await api(`/parameter_tuning_job/`, { params: { data__project: projectId }, signal });
    jobs.value = res.results ?? res;
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = true;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(fetchJobs);
</script>
