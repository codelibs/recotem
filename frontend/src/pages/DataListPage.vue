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

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>Failed to load data.</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchData"
        />
      </div>
    </Message>

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
import Message from "primevue/message";
import { api } from "@/api/client";
import { formatDate, formatFileSize } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { TrainingData } from "@/types";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const dataList = ref<TrainingData[]>([]);
const loading = ref(false);
const error = ref(false);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<TrainingData | null>(null);

async function fetchData() {
  loading.value = true;
  error.value = false;
  try {
    const res = await api(`/training_data/`, { params: { project: projectId }, signal });
    dataList.value = res.results ?? res;
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = true;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(fetchData);

const formatSize = formatFileSize;

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
