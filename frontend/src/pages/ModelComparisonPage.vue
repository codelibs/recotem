<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        @click="router.push(`/projects/${projectId}/tuning`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        {{ $t('compare.title') }}
      </h2>
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      {{ error?.message ?? t('compare.failedToLoad') }}
    </Message>

    <!-- Job Selection -->
    <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6 mb-6">
      <h3 class="font-semibold text-neutral-800 mb-4">
        {{ $t('compare.selectJobsToCompare') }}
      </h3>
      <div class="flex flex-wrap gap-3 mb-4">
        <div
          v-for="job in availableJobs"
          :key="job.id"
          class="flex items-center gap-2"
        >
          <Checkbox
            v-model="selectedJobIds"
            :input-id="`job-${job.id}`"
            :value="job.id"
            :disabled="!selectedJobIds.includes(job.id) && selectedJobIds.length >= 3"
          />
          <label
            :for="`job-${job.id}`"
            class="text-sm"
          >
            Job #{{ job.id }}
            <span
              v-if="job.best_score"
              class="text-neutral-100"
            >
              ({{ $t('tuning.score') }}: {{ job.best_score.toFixed(4) }})
            </span>
          </label>
        </div>
      </div>
      <p
        v-if="availableJobs.length === 0 && !loading"
        class="text-sm text-neutral-100"
      >
        {{ $t('compare.noCompletedJobs') }}
      </p>
    </div>

    <!-- Comparison Table -->
    <div
      v-if="selectedJobs.length >= 2"
      class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6"
    >
      <h3 class="font-semibold text-neutral-800 mb-4">
        {{ $t('compare.comparison') }}
      </h3>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-neutral-30">
              <th class="text-left py-2 pr-4 text-neutral-100 font-medium">
                {{ $t('compare.attribute') }}
              </th>
              <th
                v-for="job in selectedJobs"
                :key="job.id"
                class="text-left py-2 px-4 text-neutral-800 font-medium"
              >
                Job #{{ job.id }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr class="border-b border-neutral-30">
              <td class="py-2 pr-4 text-neutral-100">
                {{ $t('tuning.bestScore') }}
              </td>
              <td
                v-for="job in selectedJobs"
                :key="job.id"
                class="py-2 px-4"
                :class="{ 'font-bold text-success': job.id === bestJobId }"
              >
                {{ job.best_score?.toFixed(4) ?? '-' }}
              </td>
            </tr>
            <tr class="border-b border-neutral-30">
              <td class="py-2 pr-4 text-neutral-100">
                {{ $t('tuning.trials') }}
              </td>
              <td
                v-for="job in selectedJobs"
                :key="job.id"
                class="py-2 px-4"
              >
                {{ job.n_trials }}
              </td>
            </tr>
            <tr class="border-b border-neutral-30">
              <td class="py-2 pr-4 text-neutral-100">
                {{ $t('compare.parallelTasks') }}
              </td>
              <td
                v-for="job in selectedJobs"
                :key="job.id"
                class="py-2 px-4"
              >
                {{ job.n_tasks_parallel }}
              </td>
            </tr>
            <tr class="border-b border-neutral-30">
              <td class="py-2 pr-4 text-neutral-100">
                {{ $t('compare.memoryBudget') }}
              </td>
              <td
                v-for="job in selectedJobs"
                :key="job.id"
                class="py-2 px-4"
              >
                {{ job.memory_budget }} MB
              </td>
            </tr>
            <tr class="border-b border-neutral-30">
              <td class="py-2 pr-4 text-neutral-100">
                {{ $t('models.irspackVersion') }}
              </td>
              <td
                v-for="job in selectedJobs"
                :key="job.id"
                class="py-2 px-4"
              >
                {{ job.irspack_version ?? '-' }}
              </td>
            </tr>
            <tr>
              <td class="py-2 pr-4 text-neutral-100">
                {{ $t('common.createdAt') }}
              </td>
              <td
                v-for="job in selectedJobs"
                :key="job.id"
                class="py-2 px-4"
              >
                {{ formatDate(job.ins_datetime) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div
      v-else-if="selectedJobIds.length > 0 && selectedJobIds.length < 2"
      class="text-sm text-neutral-100 mt-4"
    >
      {{ $t('compare.selectAtLeast2') }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Button from "primevue/button";
import Checkbox from "primevue/checkbox";
import Message from "primevue/message";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import type { ClassifiedApiError, ParameterTuningJob } from "@/types";
import { ENDPOINTS } from "@/api/endpoints";
import { classifyApiError, unwrapResults } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = route.params.projectId as string;
const availableJobs = ref<ParameterTuningJob[]>([]);
const selectedJobIds = ref<number[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);

const selectedJobs = computed(() =>
  availableJobs.value.filter(j => selectedJobIds.value.includes(j.id))
);

const bestJobId = computed(() => {
  const jobs = selectedJobs.value.filter(j => j.best_score != null);
  if (jobs.length === 0) return null;
  return jobs.reduce((best, job) => (job.best_score! > best.best_score! ? job : best)).id;
});

onMounted(async () => {
  loading.value = true;
  error.value = null;
  try {
    const res = await api(ENDPOINTS.PARAMETER_TUNING_JOB, { params: { data__project: projectId } });
    const jobs: ParameterTuningJob[] = unwrapResults(res);
    // Show only jobs that have a best_config (completed)
    availableJobs.value = jobs.filter(j => j.best_config != null);
  } catch (e) {
    error.value = classifyApiError(e);
  } finally {
    loading.value = false;
  }
});

</script>
