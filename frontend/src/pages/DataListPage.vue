<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        Training Data
      </h2>
      <Button
        label="Upload Data"
        icon="pi pi-upload"
        @click="router.push(`/projects/${projectId}/data/upload`)"
      />
    </div>

    <DataTable
      :value="dataList"
      :loading="loading"
      striped-rows
      paginator
      :rows="20"
      empty-message="No data uploaded yet"
    >
      <Column
        field="basename"
        header="File Name"
        sortable
      >
        <template #body="{ data }">
          <router-link
            :to="`/projects/${projectId}/data/${data.id}`"
            class="text-primary hover:underline"
          >
            {{ data.basename }}
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
        field="ins_datetime"
        header="Uploaded"
        sortable
      >
        <template #body="{ data }">
          {{ formatDate(data.ins_datetime) }}
        </template>
      </Column>
      <Column
        header="Actions"
        :style="{ width: '120px' }"
      >
        <template #body="{ data }">
          <Button
            icon="pi pi-trash"
            severity="danger"
            text
            rounded
            :aria-label="`Delete ${data.basename}`"
            @click="confirmDelete(data)"
          />
        </template>
      </Column>
    </DataTable>

    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      header="Delete Data"
      :message="`Are you sure you want to delete ${deleteTarget?.basename}?`"
      confirm-label="Delete"
      cancel-label="Cancel"
      danger
      @confirm="executeDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import dayjs from "dayjs";
import { api } from "@/api/client";
import { useNotification } from "@/composables/useNotification";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { TrainingData } from "@/types";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const projectId = route.params.projectId as string;
const dataList = ref<TrainingData[]>([]);
const loading = ref(false);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<TrainingData | null>(null);

onMounted(async () => {
  loading.value = true;
  try {
    const res = await api(`/training_data/`, { params: { project: projectId } });
    dataList.value = res.results ?? res;
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

function confirmDelete(data: TrainingData) {
  deleteTarget.value = data;
  showDeleteConfirm.value = true;
}

async function executeDelete() {
  if (!deleteTarget.value) return;
  try {
    await api(`/training_data/${deleteTarget.value.id}/`, { method: "DELETE" });
    dataList.value = dataList.value.filter(d => d.id !== deleteTarget.value!.id);
    notify.success("Data deleted");
  } catch {
    notify.error("Failed to delete data");
  }
  deleteTarget.value = null;
}
</script>
