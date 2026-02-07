<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        Trained Models
      </h2>
      <Button
        label="Train Model"
        icon="pi pi-plus"
        @click="router.push(`/projects/${projectId}/models/train`)"
      />
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-4">
      Failed to load models. Please try again.
    </Message>

    <DataTable
      :value="models"
      :loading="loading"
      striped-rows
      paginator
      :rows="20"
      empty-message="No trained models yet"
    >
      <Column
        field="id"
        header="ID"
        sortable
        :style="{ width: '80px' }"
      />
      <Column header="Algorithm">
        <template #body="{ data }">
          <router-link
            :to="`/projects/${projectId}/models/${data.id}`"
            class="text-primary hover:underline"
          >
            {{ data.basename || `Model #${data.id}` }}
          </router-link>
        </template>
      </Column>
      <Column
        field="filesize"
        header="Size"
        sortable
      >
        <template #body="{ data }">
          {{ formatSize(data.filesize) }}
        </template>
      </Column>
      <Column
        field="irspack_version"
        header="irspack"
      />
      <Column
        field="ins_datetime"
        header="Trained"
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
            @click="router.push(`/projects/${projectId}/models/${data.id}`)"
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
import dayjs from "dayjs";
import { api } from "@/api/client";
import type { TrainedModel } from "@/types";

const route = useRoute();
const router = useRouter();
const projectId = route.params.projectId as string;
const models = ref<TrainedModel[]>([]);
const loading = ref(false);
const error = ref(false);

onMounted(async () => {
  loading.value = true;
  error.value = false;
  try {
    const res = await api(`/trained_model/`, { params: { data_loc__project: projectId } });
    models.value = res.results ?? res;
  } catch {
    error.value = true;
  } finally {
    loading.value = false;
  }
});

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function formatDate(dt: string) {
  return dayjs(dt).format("MMM D, YYYY HH:mm");
}
</script>
