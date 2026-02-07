<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        @click="router.push(`/projects/${projectId}/data`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        {{ data?.basename ?? 'Data Detail' }}
      </h2>
    </div>

    <Message v-if="error" severity="error" :closable="false" class="mb-4">
      Failed to load data details. Please try again.
    </Message>

    <div v-if="loading" class="space-y-4">
      <Skeleton height="2rem" width="60%" />
      <Skeleton height="8rem" />
    </div>

    <div
      v-else-if="data"
      class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6"
    >
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
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import dayjs from "dayjs";
import { api } from "@/api/client";
import type { TrainingData } from "@/types";

const route = useRoute();
const router = useRouter();
const projectId = route.params.projectId as string;
const dataId = route.params.dataId as string;
const data = ref<TrainingData | null>(null);
const loading = ref(false);
const error = ref(false);

onMounted(async () => {
  loading.value = true;
  error.value = false;
  try {
    data.value = await api(`/training_data/${dataId}/`);
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

function downloadFile() {
  if (data.value) window.open(data.value.file, "_blank");
}
</script>
