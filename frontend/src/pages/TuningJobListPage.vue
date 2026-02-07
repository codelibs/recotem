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

    <Message v-if="error" severity="error" :closable="false" class="mb-4">
      Failed to load tuning jobs. Please try again.
    </Message>

    <DataTable
      :value="jobs"
      :loading="loading"
      striped-rows
      paginator
      :rows="20"
      empty-message="No tuning jobs yet"
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
            :severity="data.best_config ? 'success' : 'info'"
            :value="data.best_config ? 'Complete' : 'Pending'"
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
            @click="router.push(`/projects/${projectId}/tuning/${data.id}`)"
          />
        </template>
      </Column>
    </DataTable>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Tag from "primevue/tag";
import dayjs from "dayjs";
import { api } from "@/api/client";
import type { ParameterTuningJob } from "@/types";

const route = useRoute();
const router = useRouter();
const projectId = route.params.projectId as string;
const jobs = ref<ParameterTuningJob[]>([]);
const loading = ref(false);
const error = ref(false);

onMounted(async () => {
  loading.value = true;
  error.value = false;
  try {
    const res = await api(`/parameter_tuning_job/`, { params: { data__project: projectId } });
    jobs.value = res.results ?? res;
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
