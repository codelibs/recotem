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
        Train Model
      </h2>
    </div>

    <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6 max-w-xl">
      <div class="space-y-4">
        <div>
          <label class="block text-sm font-medium mb-1">Training Data</label>
          <Select
            v-model="form.data"
            :options="dataOptions"
            option-label="basename"
            option-value="id"
            placeholder="Select data"
            class="w-full"
          />
        </div>
        <div>
          <label class="block text-sm font-medium mb-1">Model Configuration</label>
          <Select
            v-model="form.config"
            :options="configOptions"
            option-label="label"
            option-value="id"
            placeholder="Select configuration"
            class="w-full"
          />
        </div>
      </div>
      <div class="mt-6">
        <Button
          label="Start Training"
          icon="pi pi-play"
          :loading="submitting"
          :disabled="!form.data || !form.config"
          @click="submitTraining"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import Select from "primevue/select";
import Button from "primevue/button";
import { api } from "@/api/client";
import { useNotification } from "@/composables/useNotification";
import type { TrainingData, ModelConfiguration } from "@/types";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const projectId = route.params.projectId as string;
const submitting = ref(false);
const dataOptions = ref<TrainingData[]>([]);
const configOptions = ref<{ id: number; label: string }[]>([]);

const form = reactive({ data: null as number | null, config: null as number | null });

onMounted(async () => {
  const [dataRes, configRes] = await Promise.all([
    api(`/training_data/`, { params: { project: projectId } }),
    api(`/model_configuration/`, { params: { project: projectId } }),
  ]);
  dataOptions.value = dataRes.results ?? dataRes;
  const configs: ModelConfiguration[] = configRes.results ?? configRes;
  configOptions.value = configs.map(c => ({ id: c.id, label: c.name || `${c.recommender_class_name} #${c.id}` }));
});

async function submitTraining() {
  if (!form.data || !form.config) return;
  submitting.value = true;
  try {
    const model = await api("/trained_model/", {
      method: "POST",
      body: { data_loc: form.data, configuration: form.config },
    });
    notify.success("Training started");
    router.push(`/projects/${projectId}/models/${model.id}`);
  } catch {
    notify.error("Failed to start training");
  } finally {
    submitting.value = false;
  }
}
</script>
