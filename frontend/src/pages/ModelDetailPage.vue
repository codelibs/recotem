<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        @click="router.push(`/projects/${projectId}/models`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        Model #{{ modelId }}
      </h2>
    </div>

    <div
      v-if="model"
      class="space-y-6"
    >
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <h3 class="font-semibold text-neutral-800 mb-4">
          Model Details
        </h3>
        <div class="grid grid-cols-2 gap-4 text-sm">
          <div><span class="text-neutral-100">File:</span> {{ model.basename }}</div>
          <div><span class="text-neutral-100">Size:</span> {{ formatSize(model.filesize) }}</div>
          <div><span class="text-neutral-100">irspack:</span> {{ model.irspack_version ?? 'N/A' }}</div>
          <div><span class="text-neutral-100">Trained:</span> {{ formatDate(model.ins_datetime) }}</div>
        </div>
      </div>

      <!-- Recommendation Preview -->
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <h3 class="font-semibold text-neutral-800 mb-4">
          Recommendation Preview
        </h3>
        <div class="flex gap-3 mb-4">
          <InputText
            v-model="userId"
            placeholder="Enter user ID"
            class="flex-1"
          />
          <InputNumber
            v-model="topK"
            :min="1"
            :max="100"
            placeholder="Top K"
            :style="{ width: '120px' }"
          />
          <Button
            label="Get Recommendations"
            icon="pi pi-search"
            :loading="recLoading"
            @click="fetchRecommendations"
          />
        </div>
        <DataTable
          v-if="recommendations.length"
          :value="recommendations"
          striped-rows
        >
          <Column
            field="item_id"
            header="Item ID"
          />
          <Column
            field="score"
            header="Score"
          >
            <template #body="{ data }">
              {{ data.score.toFixed(4) }}
            </template>
          </Column>
        </DataTable>
        <div
          v-else-if="recFetched"
          class="text-center py-4 text-neutral-200"
        >
          No recommendations found
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import InputText from "primevue/inputtext";
import InputNumber from "primevue/inputnumber";
import Button from "primevue/button";
import dayjs from "dayjs";
import { api } from "@/api/client";
import { useNotification } from "@/composables/useNotification";
import type { TrainedModel, Recommendation } from "@/types";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const projectId = route.params.projectId as string;
const modelId = route.params.modelId as string;
const model = ref<TrainedModel | null>(null);
const userId = ref("");
const topK = ref(10);
const recommendations = ref<Recommendation[]>([]);
const recLoading = ref(false);
const recFetched = ref(false);

onMounted(async () => {
  model.value = await api(`/trained_model/${modelId}/`);
});

async function fetchRecommendations() {
  if (!userId.value) return;
  recLoading.value = true;
  recFetched.value = false;
  try {
    const res = await api(`/trained_model/${modelId}/recommendation/`, {
      params: { user_id: userId.value, cutoff: topK.value },
    });
    recommendations.value = res;
    recFetched.value = true;
  } catch {
    notify.error("Failed to get recommendations");
  } finally {
    recLoading.value = false;
  }
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function formatDate(dt: string) {
  return dayjs(dt).format("MMM D, YYYY HH:mm");
}
</script>
