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
        New Tuning Job
      </h2>
    </div>

    <Stepper :value="activeStep">
      <StepList>
        <Step :value="0">
          Data
        </Step>
        <Step :value="1">
          Split
        </Step>
        <Step :value="2">
          Evaluation
        </Step>
        <Step :value="3">
          Run
        </Step>
      </StepList>
      <StepPanels>
        <StepPanel :value="0">
          <div class="p-4">
            <h3 class="font-semibold mb-4">
              Select Training Data
            </h3>
            <Select
              v-model="form.data"
              :options="dataOptions"
              option-label="basename"
              option-value="id"
              placeholder="Select data"
              class="w-full"
            />
            <div class="mt-6 flex justify-end">
              <Button
                label="Next"
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
              Split Configuration
            </h3>
            <div class="space-y-4">
              <div>
                <label class="block text-sm font-medium mb-1">Scheme</label>
                <Select
                  v-model="form.splitScheme"
                  :options="splitSchemes"
                  option-label="label"
                  option-value="value"
                  class="w-full"
                />
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Heldout Ratio</label>
                <InputNumber
                  v-model="form.heldoutRatio"
                  :min="0.01"
                  :max="0.99"
                  :step="0.05"
                  :min-fraction-digits="2"
                  class="w-full"
                />
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Test User Ratio</label>
                <InputNumber
                  v-model="form.testUserRatio"
                  :min="0.01"
                  :max="0.99"
                  :step="0.05"
                  :min-fraction-digits="2"
                  class="w-full"
                />
              </div>
            </div>
            <div class="mt-6 flex justify-between">
              <Button
                label="Back"
                icon="pi pi-arrow-left"
                severity="secondary"
                @click="activeStep = 0"
              />
              <Button
                label="Next"
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
              Evaluation Configuration
            </h3>
            <div class="space-y-4">
              <div>
                <label class="block text-sm font-medium mb-1">Target Metric</label>
                <Select
                  v-model="form.targetMetric"
                  :options="metrics"
                  option-label="label"
                  option-value="value"
                  class="w-full"
                />
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Cutoff</label>
                <InputNumber
                  v-model="form.cutoff"
                  :min="1"
                  :max="100"
                  class="w-full"
                />
              </div>
            </div>
            <div class="mt-6 flex justify-between">
              <Button
                label="Back"
                icon="pi pi-arrow-left"
                severity="secondary"
                @click="activeStep = 1"
              />
              <Button
                label="Next"
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
              Job Configuration
            </h3>
            <div class="space-y-4">
              <div>
                <label class="block text-sm font-medium mb-1">Number of Trials</label>
                <InputNumber
                  v-model="form.nTrials"
                  :min="1"
                  :max="200"
                  class="w-full"
                />
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Parallel Tasks</label>
                <InputNumber
                  v-model="form.nParallel"
                  :min="1"
                  :max="8"
                  class="w-full"
                />
              </div>
              <div>
                <label class="block text-sm font-medium mb-1">Memory Budget (MB)</label>
                <InputNumber
                  v-model="form.memoryBudget"
                  :min="128"
                  :step="128"
                  class="w-full"
                />
              </div>
              <div class="flex items-center gap-2">
                <Checkbox
                  v-model="form.trainAfterTuning"
                  :binary="true"
                  input-id="train-after"
                />
                <label
                  for="train-after"
                  class="text-sm"
                >Train model after tuning completes</label>
              </div>
            </div>
            <div class="mt-6 flex justify-between">
              <Button
                label="Back"
                icon="pi pi-arrow-left"
                severity="secondary"
                @click="activeStep = 2"
              />
              <Button
                label="Start Tuning"
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
import Stepper from "primevue/stepper";
import StepList from "primevue/steplist";
import Step from "primevue/step";
import StepPanels from "primevue/steppanels";
import StepPanel from "primevue/steppanel";
import Select from "primevue/select";
import InputNumber from "primevue/inputnumber";
import Checkbox from "primevue/checkbox";
import Button from "primevue/button";
import { api } from "@/api/client";
import { useNotification } from "@/composables/useNotification";
import type { TrainingData } from "@/types";

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
  const res = await api(`/training_data/`, { params: { project: projectId } });
  dataOptions.value = res.results ?? res;
});

async function submitJob() {
  if (!form.data) return;
  submitting.value = true;
  try {
    // Create split config
    const split = await api("/split_config/", {
      method: "POST",
      body: { scheme: form.splitScheme, heldout_ratio: form.heldoutRatio, test_user_ratio: form.testUserRatio, random_seed: 42 },
    });
    // Create evaluation config
    const evaluation = await api("/evaluation_config/", {
      method: "POST",
      body: { target_metric: form.targetMetric, cutoff: form.cutoff },
    });
    // Create tuning job
    const job = await api("/parameter_tuning_job/", {
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
    notify.success("Tuning job created");
    router.push(`/projects/${projectId}/tuning/${job.id}`);
  } catch {
    notify.error("Failed to create tuning job");
  } finally {
    submitting.value = false;
  }
}
</script>
