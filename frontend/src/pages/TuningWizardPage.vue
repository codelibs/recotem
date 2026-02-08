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
        {{ $t('tuning.newJob') }}
      </h2>
    </div>

    <Stepper :value="activeStep">
      <StepList>
        <Step :value="0">
          {{ $t('tuning.stepData') }}
        </Step>
        <Step :value="1">
          {{ $t('tuning.stepSplit') }}
        </Step>
        <Step :value="2">
          {{ $t('tuning.stepEvaluation') }}
        </Step>
        <Step :value="3">
          {{ $t('tuning.stepRun') }}
        </Step>
      </StepList>
      <StepPanels>
        <StepPanel :value="0">
          <div class="p-4">
            <h3 class="font-semibold mb-4">
              {{ $t('tuning.selectTrainingData') }}
            </h3>
            <Select
              v-model="form.data"
              :options="dataOptions"
              option-label="basename"
              option-value="id"
              :placeholder="$t('tuning.selectData')"
              class="w-full"
            />
            <div class="mt-6 flex justify-end">
              <Button
                :label="$t('common.next')"
                icon="pi pi-arrow-right"
                icon-pos="right"
                :disabled="!form.data"
                @click="activeStep = 1"
              />
            </div>
          </div>
        </StepPanel>
        <StepPanel :value="1">
          <div class="p-4">
            <h3 class="font-semibold mb-4">
              {{ $t('tuning.splitConfiguration') }}
            </h3>
            <div class="space-y-4">
              <FormField
                :label="$t('tuning.scheme')"
                name="scheme"
                :tooltip="$t('tuning.schemeHelp')"
              >
                <Select
                  v-model="form.splitScheme"
                  :options="splitSchemes"
                  option-label="label"
                  option-value="value"
                  class="w-full"
                />
              </FormField>
              <FormField
                :label="$t('tuning.heldoutRatio')"
                name="heldout-ratio"
                :tooltip="$t('tuning.heldoutRatioHelp')"
              >
                <InputNumber
                  v-model="form.heldoutRatio"
                  :min="0.01"
                  :max="0.99"
                  :step="0.05"
                  :min-fraction-digits="2"
                  class="w-full"
                />
              </FormField>
              <FormField
                :label="$t('tuning.testUserRatio')"
                name="test-user-ratio"
                :tooltip="$t('tuning.testUserRatioHelp')"
              >
                <InputNumber
                  v-model="form.testUserRatio"
                  :min="0.01"
                  :max="0.99"
                  :step="0.05"
                  :min-fraction-digits="2"
                  class="w-full"
                />
              </FormField>
            </div>
            <div class="mt-6 flex justify-between">
              <Button
                :label="$t('common.back')"
                icon="pi pi-arrow-left"
                severity="secondary"
                @click="activeStep = 0"
              />
              <Button
                :label="$t('common.next')"
                icon="pi pi-arrow-right"
                icon-pos="right"
                @click="activeStep = 2"
              />
            </div>
          </div>
        </StepPanel>
        <StepPanel :value="2">
          <div class="p-4">
            <h3 class="font-semibold mb-4">
              {{ $t('tuning.evaluationConfiguration') }}
            </h3>
            <div class="space-y-4">
              <FormField
                :label="$t('tuning.targetMetric')"
                name="target-metric"
                :tooltip="$t('tuning.targetMetricHelp')"
              >
                <Select
                  v-model="form.targetMetric"
                  :options="metrics"
                  option-label="label"
                  option-value="value"
                  class="w-full"
                />
              </FormField>
              <FormField
                :label="$t('tuning.cutoff')"
                name="cutoff"
                :tooltip="$t('tuning.cutoffHelp')"
              >
                <InputNumber
                  v-model="form.cutoff"
                  :min="1"
                  :max="100"
                  class="w-full"
                />
              </FormField>
            </div>
            <div class="mt-6 flex justify-between">
              <Button
                :label="$t('common.back')"
                icon="pi pi-arrow-left"
                severity="secondary"
                @click="activeStep = 1"
              />
              <Button
                :label="$t('common.next')"
                icon="pi pi-arrow-right"
                icon-pos="right"
                @click="activeStep = 3"
              />
            </div>
          </div>
        </StepPanel>
        <StepPanel :value="3">
          <div class="p-4">
            <h3 class="font-semibold mb-4">
              {{ $t('tuning.jobConfiguration') }}
            </h3>
            <div class="space-y-4">
              <FormField
                :label="$t('tuning.nTrials')"
                name="n-trials"
                :tooltip="$t('tuning.nTrialsHelp')"
              >
                <InputNumber
                  v-model="form.nTrials"
                  :min="1"
                  :max="200"
                  class="w-full"
                />
              </FormField>
              <FormField
                :label="$t('tuning.parallelTasks')"
                name="n-parallel"
                :tooltip="$t('tuning.nParallelHelp')"
              >
                <InputNumber
                  v-model="form.nParallel"
                  :min="1"
                  :max="8"
                  class="w-full"
                />
              </FormField>
              <FormField
                :label="$t('tuning.memoryBudget')"
                name="memory-budget"
                :tooltip="$t('tuning.memoryBudgetHelp')"
              >
                <InputNumber
                  v-model="form.memoryBudget"
                  :min="128"
                  :step="128"
                  class="w-full"
                />
              </FormField>
              <div class="flex items-center gap-2">
                <Checkbox
                  v-model="form.trainAfterTuning"
                  :binary="true"
                  input-id="train-after"
                />
                <label
                  for="train-after"
                  class="text-sm"
                >{{ $t('tuning.trainAfterTuningLabel') }}</label>
              </div>
            </div>
            <div class="mt-6 flex justify-between">
              <Button
                :label="$t('common.back')"
                icon="pi pi-arrow-left"
                severity="secondary"
                @click="activeStep = 2"
              />
              <Button
                :label="$t('tuning.startJob')"
                icon="pi pi-play"
                :loading="submitting"
                @click="submitJob"
              />
            </div>
          </div>
        </StepPanel>
      </StepPanels>
    </Stepper>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Stepper from "primevue/stepper";
import StepList from "primevue/steplist";
import Step from "primevue/step";
import StepPanels from "primevue/steppanels";
import StepPanel from "primevue/steppanel";
import Select from "primevue/select";
import InputNumber from "primevue/inputnumber";
import Checkbox from "primevue/checkbox";
import Button from "primevue/button";
import { api, unwrapResults } from "@/api/client";
import { ENDPOINTS } from "@/api/endpoints";
import { useNotification } from "@/composables/useNotification";
import FormField from "@/components/common/FormField.vue";
import type { TrainingData } from "@/types";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const notify = useNotification();
const projectId = route.params.projectId as string;
const activeStep = ref(0);
const submitting = ref(false);
const dataOptions = ref<TrainingData[]>([]);

const form = reactive({
  data: route.query.dataId ? Number(route.query.dataId) : null as number | null,
  splitScheme: "RG" as string,
  heldoutRatio: 0.2,
  testUserRatio: 0.5,
  targetMetric: "ndcg" as string,
  cutoff: 10,
  nTrials: 40,
  nParallel: 2,
  memoryBudget: 2048,
  trainAfterTuning: true,
});

const splitSchemes = [
  { label: "Random Global", value: "RG" },
  { label: "Time Global", value: "TG" },
  { label: "Time User", value: "TU" },
];

const metrics = [
  { label: "NDCG", value: "ndcg" },
  { label: "MAP", value: "map" },
  { label: "Recall", value: "recall" },
  { label: "Hit Rate", value: "hit" },
];

onMounted(async () => {
  const res = await api(ENDPOINTS.TRAINING_DATA, { params: { project: projectId } });
  dataOptions.value = unwrapResults(res);
});

async function submitJob() {
  if (!form.data) return;
  submitting.value = true;
  try {
    // Create split config
    const split = await api(ENDPOINTS.SPLIT_CONFIG, {
      method: "POST",
      body: { scheme: form.splitScheme, heldout_ratio: form.heldoutRatio, test_user_ratio: form.testUserRatio, random_seed: 42 },
    });
    // Create evaluation config
    const evaluation = await api(ENDPOINTS.EVALUATION_CONFIG, {
      method: "POST",
      body: { target_metric: form.targetMetric, cutoff: form.cutoff },
    });
    // Create tuning job
    const job = await api(ENDPOINTS.PARAMETER_TUNING_JOB, {
      method: "POST",
      body: {
        data: form.data,
        split: split.id,
        evaluation: evaluation.id,
        n_tasks_parallel: form.nParallel,
        n_trials: form.nTrials,
        memory_budget: form.memoryBudget,
        train_after_tuning: form.trainAfterTuning,
      },
    });
    notify.success(t("tuning.jobCreated"));
    router.push(`/projects/${projectId}/tuning/${job.id}`);
  } catch {
    notify.error(t("tuning.jobCreateFailed"));
  } finally {
    submitting.value = false;
  }
}
</script>
