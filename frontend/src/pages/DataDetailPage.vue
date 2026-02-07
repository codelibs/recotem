<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        aria-label="Back to data list"
        @click="router.push(`/projects/${projectId}/data`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        {{ data?.basename ?? 'Data Detail' }}
      </h2>
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      Failed to load data details. Please try again.
    </Message>

    <div
      v-if="loading"
      class="space-y-4"
    >
      <Skeleton
        height="2rem"
        width="60%"
      />
      <Skeleton height="8rem" />
    </div>

    <div
      v-else-if="data"
      class="space-y-6"
    >
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <div class="grid grid-cols-2 gap-4 text-sm">
          <div><span class="text-neutral-100">File:</span> <span class="text-neutral-800">{{ data.basename }}</span></div>
          <div><span class="text-neutral-100">Size:</span> <span class="text-neutral-800">{{ formatSize(data.filesize) }}</span></div>
          <div><span class="text-neutral-100">Uploaded:</span> <span class="text-neutral-800">{{ formatDate(data.ins_datetime) }}</span></div>
          <div><span class="text-neutral-100">ID:</span> <span class="text-neutral-800">{{ data.id }}</span></div>
        </div>
        <div class="mt-6 flex gap-3">
          <Button
            label="Start Tuning"
            icon="pi pi-play"
            @click="router.push({ name: 'tuning-new', query: { dataId: data.id.toString() } })"
          />
          <Button
            label="Download"
            icon="pi pi-download"
            severity="secondary"
            @click="downloadFile"
          />
        </div>
      </div>

      <!-- Data Preview -->
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-neutral-800">
            Data Preview
          </h3>
          <span
            v-if="preview"
            class="text-sm text-neutral-100"
          >
            {{ preview.total_rows }} total rows
          </span>
        </div>

        <div
          v-if="previewLoading"
          class="space-y-2"
        >
          <Skeleton height="2rem" />
          <Skeleton height="8rem" />
        </div>

        <div
          v-else-if="preview && preview.columns.length > 0"
          class="overflow-x-auto"
        >
          <DataTable
            :value="previewRows"
            striped-rows
            scrollable
            scroll-height="400px"
            :rows="50"
            size="small"
          >
            <Column
              v-for="col in preview.columns"
              :key="col"
              :field="col"
              :header="col"
              style="min-width: 120px"
            />
          </DataTable>
        </div>

        <div
          v-else
          class="text-sm text-neutral-100"
        >
          Unable to load data preview.
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import { api } from "@/api/client";
import { formatDate, formatFileSize } from "@/utils/format";
import type { TrainingData } from "@/types";

const route = useRoute();
const router = useRouter();
const projectId = route.params.projectId as string;
const dataId = route.params.dataId as string;
const data = ref<TrainingData | null>(null);
const loading = ref(false);
const error = ref(false);
const preview = ref<{ columns: string[]; rows: unknown[][]; total_rows: number } | null>(null);
const previewLoading = ref(false);

const previewRows = computed(() => {
  if (!preview.value) return [];
  return preview.value.rows.map((row) => {
    const obj: Record<string, unknown> = {};
    preview.value!.columns.forEach((col, i) => {
      obj[col] = row[i];
    });
    return obj;
  });
});

onMounted(async () => {
  loading.value = true;
  error.value = false;
  try {
    data.value = await api(`/training_data/${dataId}/`);
    loadPreview();
  } catch {
    error.value = true;
  } finally {
    loading.value = false;
  }
});

async function loadPreview() {
  previewLoading.value = true;
  try {
    preview.value = await api(`/training_data/${dataId}/preview/`, { params: { n_rows: 50 } });
  } catch {
    preview.value = null;
  } finally {
    previewLoading.value = false;
  }
}

const formatSize = formatFileSize;

function downloadFile() {
  if (data.value) window.open(data.value.file, "_blank");
}
</script>
