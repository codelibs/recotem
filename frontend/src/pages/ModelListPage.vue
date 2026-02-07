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

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>Failed to load models.</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchModels"
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
      v-else-if="!error && models.length === 0"
      class="text-center py-16"
    >
      <i class="pi pi-box text-5xl text-neutral-40 mb-4" />
      <h3 class="text-lg font-medium text-neutral-500">
        No trained models yet
      </h3>
      <p class="text-neutral-200 mt-1 mb-4">
        Train a model from your tuning results
      </p>
      <Button
        label="Train Model"
        icon="pi pi-plus"
        @click="router.push(`/projects/${projectId}/models/train`)"
      />
    </div>

    <div
      v-else
      class="overflow-x-auto"
    >
      <DataTable
        :value="models"
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
          class="hidden md:table-cell"
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
          :style="{ width: '120px' }"
        >
          <template #body="{ data }">
            <Button
              icon="pi pi-eye"
              text
              rounded
              :aria-label="`View model #${data.id}`"
              @click="router.push(`/projects/${projectId}/models/${data.id}`)"
            />
            <Button
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              :aria-label="`Delete model #${data.id}`"
              @click="confirmDelete(data)"
            />
          </template>
        </Column>
      </DataTable>
    </div>

    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      header="Delete Model"
      :message="`Are you sure you want to delete model #${deleteTarget?.id}? This cannot be undone.`"
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
import Skeleton from "primevue/skeleton";
import { api } from "@/api/client";
import { formatDate, formatFileSize } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { TrainedModel } from "@/types";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const models = ref<TrainedModel[]>([]);
const loading = ref(false);
const error = ref(false);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<TrainedModel | null>(null);

async function fetchModels() {
  loading.value = true;
  error.value = false;
  try {
    const res = await api(`/trained_model/`, { params: { data_loc__project: projectId }, signal });
    models.value = res.results ?? res;
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = true;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(fetchModels);

const formatSize = formatFileSize;

function confirmDelete(model: TrainedModel) {
  deleteTarget.value = model;
  showDeleteConfirm.value = true;
}

async function executeDelete() {
  if (!deleteTarget.value) return;
  try {
    await api(`/trained_model/${deleteTarget.value.id}/`, { method: "DELETE" });
    models.value = models.value.filter(m => m.id !== deleteTarget.value!.id);
    notify.success("Model deleted");
  } catch {
    notify.error("Failed to delete model");
  }
  deleteTarget.value = null;
}
</script>
