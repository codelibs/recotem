<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        :aria-label="$t('common.back')"
        @click="router.push(`/projects/${projectId}/models`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        {{ $t('models.detail') }} #{{ modelId }}
      </h2>
    </div>

    <div
      v-if="model"
      class="space-y-6"
    >
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <h3 class="font-semibold text-neutral-800 mb-4">
          {{ $t('models.detail') }}
        </h3>
        <div class="grid grid-cols-2 gap-4 text-sm">
          <div><span class="text-neutral-100">{{ $t('models.file') }}:</span> {{ model.basename }}</div>
          <div><span class="text-neutral-100">{{ $t('models.size') }}:</span> {{ formatSize(model.filesize) }}</div>
          <div><span class="text-neutral-100">irspack:</span> {{ model.irspack_version ?? 'N/A' }}</div>
          <div><span class="text-neutral-100">{{ $t('models.trained') }}:</span> {{ formatDate(model.ins_datetime) }}</div>
        </div>
      </div>

      <!-- Recommendation Preview -->
      <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6">
        <h3 class="font-semibold text-neutral-800 mb-4">
          {{ $t('models.recommendationPreview') }}
        </h3>
        <div class="flex gap-3 mb-4">
          <InputText
            v-model="userId"
            :placeholder="$t('models.enterUserId')"
            class="flex-1"
          />
          <InputNumber
            v-model="topK"
            :min="1"
            :max="100"
            :placeholder="$t('models.topK')"
            :style="{ width: '120px' }"
          />
          <Button
            :label="$t('models.getRecommendations')"
            icon="pi pi-search"
            :loading="recLoading"
            @click="fetchRecommendations"
          />
        </div>
        <DataTable
          v-if="sortedRecommendations.length"
          :value="sortedRecommendations"
          striped-rows
        >
          <Column
            field="item_id"
            :header="$t('models.itemId')"
          />
          <Column
            field="score"
            :header="$t('tuning.score')"
            :sortable="true"
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
          {{ $t('models.noRecommendations') }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import InputText from "primevue/inputtext";
import InputNumber from "primevue/inputnumber";
import Button from "primevue/button";
import { api } from "@/api/client";
import { ENDPOINTS } from "@/api/endpoints";
import { formatDate, formatFileSize } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import type { TrainedModel, Recommendation } from "@/types";

const { t } = useI18n();
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

const sortedRecommendations = computed(() =>
  [...recommendations.value].sort((a, b) => b.score - a.score),
);

onMounted(async () => {
  model.value = await api(ENDPOINTS.TRAINED_MODEL_DETAIL(Number(modelId)));
});

async function fetchRecommendations() {
  if (!userId.value) return;
  recLoading.value = true;
  recFetched.value = false;
  try {
    const res = await api(ENDPOINTS.TRAINED_MODEL_RECOMMENDATION(Number(modelId)), {
      params: { user_id: userId.value, cutoff: topK.value },
    });
    recommendations.value = res;
    recFetched.value = true;
  } catch (e: any) {
    const detail = e?.data?.detail ?? t("models.failedToGetRecommendations");
    notify.error(detail);
    recFetched.value = true;
  } finally {
    recLoading.value = false;
  }
}

const formatSize = formatFileSize;
</script>
