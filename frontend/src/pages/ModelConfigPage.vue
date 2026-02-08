<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        {{ $t('configs.title') }}
      </h2>
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      {{ error?.message ?? t('configs.failedToLoad') }}
    </Message>

    <DataTable
      :value="configs"
      :loading="loading"
      striped-rows
      paginator
      :rows="20"
      :empty-message="$t('configs.noConfigs')"
    >
      <Column
        field="id"
        header="ID"
        sortable
        :style="{ width: '80px' }"
      />
      <Column
        field="name"
        :header="$t('common.name')"
        sortable
      >
        <template #body="{ data }">
          {{ data.name || '(unnamed)' }}
        </template>
      </Column>
      <Column
        field="recommender_class_name"
        :header="$t('configs.algorithm')"
        sortable
      />
      <Column
        :header="$t('configs.parameters')"
      >
        <template #body="{ data }">
          <code class="text-xs bg-neutral-10 px-2 py-1 rounded">
            {{ truncateJson(data.parameters_json) }}
          </code>
        </template>
      </Column>
      <Column
        field="ins_datetime"
        :header="$t('common.createdAt')"
        sortable
      >
        <template #body="{ data }">
          {{ formatDate(data.ins_datetime) }}
        </template>
      </Column>
      <Column
        :header="$t('common.actions')"
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
      :header="$t('configs.configDetails')"
      :style="{ width: '600px' }"
      modal
    >
      <div
        v-if="selectedConfig"
        class="space-y-4"
      >
        <div class="text-sm">
          <span class="text-neutral-100">{{ $t('common.name') }}:</span>
          <span class="ml-2 text-neutral-800">{{ selectedConfig.name || '(unnamed)' }}</span>
        </div>
        <div class="text-sm">
          <span class="text-neutral-100">{{ $t('configs.algorithm') }}:</span>
          <span class="ml-2 text-neutral-800">{{ selectedConfig.recommender_class_name }}</span>
        </div>
        <div>
          <span class="text-sm text-neutral-100">{{ $t('configs.parameters') }}:</span>
          <pre class="mt-1 bg-neutral-10 rounded-md p-4 text-xs overflow-x-auto">{{ formatJson(selectedConfig.parameters_json) }}</pre>
        </div>
      </div>
    </Dialog>

    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      :header="$t('configs.deleteConfig')"
      :message="$t('configs.deleteConfirm')"
      :confirm-label="$t('common.delete')"
      :cancel-label="$t('common.cancel')"
      danger
      @confirm="executeDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Dialog from "primevue/dialog";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { ClassifiedApiError, ModelConfiguration } from "@/types";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError, unwrapResults } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const notify = useNotification();
const projectId = route.params.projectId as string;
const configs = ref<ModelConfiguration[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);
const showDetail = ref(false);
const selectedConfig = ref<ModelConfiguration | null>(null);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<ModelConfiguration | null>(null);

onMounted(async () => {
  loading.value = true;
  error.value = null;
  try {
    const res = await api(ENDPOINTS.MODEL_CONFIGURATION, { params: { project: projectId } });
    configs.value = unwrapResults(res);
  } catch (e) {
    error.value = classifyApiError(e);
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
    await api(ENDPOINTS.MODEL_CONFIGURATION_DETAIL(deleteTarget.value.id), { method: "DELETE" });
    configs.value = configs.value.filter(c => c.id !== deleteTarget.value!.id);
    notify.success(t("configs.configDeleted"));
  } catch {
    notify.error(t("configs.failedToDelete"));
  }
  deleteTarget.value = null;
}
</script>
