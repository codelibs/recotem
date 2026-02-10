<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        {{ t('data.title') }}
      </h2>
      <Button
        :label="t('data.uploadData')"
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
        <span>{{ error?.message ?? t('data.failedToLoadShort') }}</span>
        <Button
          :label="t('common.retry')"
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
      :empty-message="t('data.noDataShort')"
    >
      <Column
        field="basename"
        :header="t('data.fileName')"
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
        :header="t('data.size')"
        sortable
      >
        <template #body="{ data }">
          {{ formatSize(data.filesize) }}
        </template>
      </Column>
      <Column
        field="ins_datetime"
        :header="t('data.uploaded')"
        sortable
      >
        <template #body="{ data }">
          {{ formatDate(data.ins_datetime) }}
        </template>
      </Column>
      <Column
        :header="t('common.actions')"
        :style="{ width: '120px' }"
      >
        <template #body="{ data }">
          <Button
            icon="pi pi-trash"
            severity="danger"
            text
            rounded
            :aria-label="`${t('common.delete')} ${data.basename}`"
            @click="confirmDelete(data)"
          />
        </template>
      </Column>
    </DataTable>

    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      :header="t('data.deleteTitle')"
      :message="t('data.deleteConfirmNamed', { name: deleteTarget?.basename })"
      :confirm-label="t('common.delete')"
      :cancel-label="t('common.cancel')"
      danger
      @confirm="executeDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import { api } from "@/api/client";
import { formatDate, formatFileSize } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { ClassifiedApiError, TrainingData } from "@/types";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError, unwrapResults } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const dataList = ref<TrainingData[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<TrainingData | null>(null);

async function fetchData() {
  loading.value = true;
  error.value = null;
  try {
    const res = await api(ENDPOINTS.TRAINING_DATA, { params: { project: projectId }, signal });
    dataList.value = unwrapResults(res);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
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
    await api(ENDPOINTS.TRAINING_DATA_DETAIL(deleteTarget.value.id), { method: "DELETE" });
    dataList.value = dataList.value.filter(d => d.id !== deleteTarget.value!.id);
    notify.success(t("data.deleteSuccess"));
  } catch {
    notify.error(t("data.deleteFailed"));
  }
  deleteTarget.value = null;
}
</script>
