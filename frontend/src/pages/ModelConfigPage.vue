<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        Model Configurations
      </h2>
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      Failed to load configurations. Please try again.
    </Message>

    <DataTable
      :value="configs"
      :loading="loading"
      striped-rows
      paginator
      :rows="20"
      empty-message="No model configurations yet"
    >
      <Column
        field="id"
        header="ID"
        sortable
        :style="{ width: '80px' }"
      />
      <Column
        field="name"
        header="Name"
        sortable
      >
        <template #body="{ data }">
          {{ data.name || '(unnamed)' }}
        </template>
      </Column>
      <Column
        field="recommender_class_name"
        header="Algorithm"
        sortable
      />
      <Column
        header="Parameters"
      >
        <template #body="{ data }">
          <code class="text-xs bg-neutral-10 px-2 py-1 rounded">
            {{ truncateJson(data.parameters_json) }}
          </code>
        </template>
      </Column>
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
        :style="{ width: '120px' }"
      >
        <template #body="{ data }">
          <Button
            icon="pi pi-eye"
            text
            rounded
            @click="openDetail(data)"
          />
          <Button
            icon="pi pi-trash"
            severity="danger"
            text
            rounded
            :aria-label="`Delete config #${data.id}`"
            @click="confirmDelete(data)"
          />
        </template>
      </Column>
    </DataTable>

    <!-- Detail Dialog -->
    <Dialog
      v-model:visible="showDetail"
      header="Configuration Details"
      :style="{ width: '600px' }"
      modal
    >
      <div
        v-if="selectedConfig"
        class="space-y-4"
      >
        <div class="text-sm">
          <span class="text-neutral-100">Name:</span>
          <span class="ml-2 text-neutral-800">{{ selectedConfig.name || '(unnamed)' }}</span>
        </div>
        <div class="text-sm">
          <span class="text-neutral-100">Algorithm:</span>
          <span class="ml-2 text-neutral-800">{{ selectedConfig.recommender_class_name }}</span>
        </div>
        <div>
          <span class="text-sm text-neutral-100">Parameters:</span>
          <pre class="mt-1 bg-neutral-10 rounded-md p-4 text-xs overflow-x-auto">{{ formatJson(selectedConfig.parameters_json) }}</pre>
        </div>
      </div>
    </Dialog>

    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      header="Delete Configuration"
      :message="`Are you sure you want to delete configuration '${deleteTarget?.name || '#' + deleteTarget?.id}'?`"
      confirm-label="Delete"
      cancel-label="Cancel"
      danger
      @confirm="executeDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Dialog from "primevue/dialog";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { ModelConfiguration } from "@/types";

const route = useRoute();
const notify = useNotification();
const projectId = route.params.projectId as string;
const configs = ref<ModelConfiguration[]>([]);
const loading = ref(false);
const error = ref(false);
const showDetail = ref(false);
const selectedConfig = ref<ModelConfiguration | null>(null);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<ModelConfiguration | null>(null);

onMounted(async () => {
  loading.value = true;
  error.value = false;
  try {
    const res = await api(`/model_configuration/`, { params: { project: projectId } });
    configs.value = res.results ?? res;
  } catch {
    error.value = true;
  } finally {
    loading.value = false;
  }
});

function truncateJson(json: string) {
  if (json.length <= 60) return json;
  return json.substring(0, 57) + "...";
}

function formatJson(json: string) {
  try {
    return JSON.stringify(JSON.parse(json), null, 2);
  } catch {
    return json;
  }
}

function openDetail(config: ModelConfiguration) {
  selectedConfig.value = config;
  showDetail.value = true;
}

function confirmDelete(config: ModelConfiguration) {
  deleteTarget.value = config;
  showDeleteConfirm.value = true;
}

async function executeDelete() {
  if (!deleteTarget.value) return;
  try {
    await api(`/model_configuration/${deleteTarget.value.id}/`, { method: "DELETE" });
    configs.value = configs.value.filter(c => c.id !== deleteTarget.value!.id);
    notify.success("Configuration deleted");
  } catch {
    notify.error("Failed to delete configuration");
  }
  deleteTarget.value = null;
}
</script>
