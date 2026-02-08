<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        {{ $t('models.title') }}
      </h2>
      <Button
        :label="$t('models.trainModel')"
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
        <span>{{ error?.message ?? t('models.failedToLoad') }}</span>
        <Button
          :label="$t('common.retry')"
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

    <EmptyState
      v-else-if="!error && models.length === 0"
      icon="pi-box"
      :title="$t('models.noModels')"
      :description="$t('models.trainFirst')"
    >
      <Button
        :label="$t('models.trainModel')"
        icon="pi pi-plus"
        @click="router.push(`/projects/${projectId}/models/train`)"
      />
    </EmptyState>

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
        <Column :header="$t('compare.algorithm')">
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
          :header="$t('models.size')"
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
          :header="$t('models.trained')"
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
      :header="$t('models.deleteModel')"
      :message="t('models.deleteConfirmFull', { id: deleteTarget?.id })"
      :confirm-label="$t('common.delete')"
      :cancel-label="$t('common.cancel')"
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
import Skeleton from "primevue/skeleton";
import { api } from "@/api/client";
import { formatDate, formatFileSize } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { ClassifiedApiError, TrainedModel } from "@/types";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError, unwrapResults } from "@/api/client";
import EmptyState from "@/components/common/EmptyState.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const models = ref<TrainedModel[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<TrainedModel | null>(null);

async function fetchModels() {
  loading.value = true;
  error.value = null;
  try {
    const res = await api(ENDPOINTS.TRAINED_MODEL, { params: { data_loc__project: projectId }, signal });
    models.value = unwrapResults(res);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
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
    await api(ENDPOINTS.TRAINED_MODEL_DETAIL(deleteTarget.value.id), { method: "DELETE" });
    models.value = models.value.filter(m => m.id !== deleteTarget.value!.id);
    notify.success(t("models.modelDeleted"));
  } catch {
    notify.error(t("models.failedToDelete"));
  }
  deleteTarget.value = null;
}
</script>
