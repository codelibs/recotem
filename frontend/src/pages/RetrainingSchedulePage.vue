<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        Retraining Schedule
      </h2>
      <Button
        v-if="!schedule && !loading"
        label="Create Schedule"
        icon="pi pi-plus"
        @click="showCreateDialog = true"
      />
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>{{ error.message ?? "Failed to load schedule." }}</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchSchedule"
        />
      </div>
    </Message>

    <div
      v-if="loading"
      class="space-y-3"
    >
      <Skeleton
        v-for="i in 3"
        :key="i"
        height="3rem"
      />
    </div>

    <EmptyState
      v-else-if="!error && !schedule"
      icon="pi-clock"
      title="No retraining schedule"
      description="Set up automated retraining for this project."
    >
      <Button
        label="Create Schedule"
        icon="pi pi-plus"
        @click="showCreateDialog = true"
      />
    </EmptyState>

    <template v-else-if="schedule">
      <!-- Schedule Configuration Card -->
      <div class="bg-white border border-neutral-30 rounded-lg p-6 mb-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-semibold text-neutral-800">
            Configuration
          </h3>
          <div class="flex items-center gap-3">
            <span class="text-sm text-neutral-500">Enabled</span>
            <InputSwitch
              :model-value="schedule.is_enabled"
              @update:model-value="toggleEnabled"
            />
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-neutral-700 mb-1">Cron Expression</label>
            <div class="flex gap-2">
              <InputText
                v-model="editForm.cron_expression"
                class="flex-1"
                placeholder="0 2 * * *"
              />
              <Button
                icon="pi pi-check"
                :disabled="editForm.cron_expression === schedule.cron_expression"
                @click="saveField('cron_expression')"
              />
            </div>
            <small class="text-neutral-400 mt-1 block">e.g., "0 2 * * *" = daily at 2 AM</small>
          </div>

          <div>
            <label class="block text-sm font-medium text-neutral-700 mb-1">Training Data</label>
            <Select
              :model-value="schedule.training_data"
              :options="trainingDataOptions"
              option-label="label"
              option-value="value"
              placeholder="Use latest"
              class="w-full"
              show-clear
              @update:model-value="val => updateSchedule({ training_data: val })"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-neutral-700 mb-1">Retune Parameters</label>
            <InputSwitch
              :model-value="schedule.retune"
              @update:model-value="val => updateSchedule({ retune: val })"
            />
            <small class="text-neutral-400 mt-1 block">Run parameter tuning before training</small>
          </div>

          <div v-if="!schedule.retune">
            <label class="block text-sm font-medium text-neutral-700 mb-1">Model Configuration</label>
            <Select
              :model-value="schedule.model_configuration"
              :options="modelConfigOptions"
              option-label="label"
              option-value="value"
              placeholder="Select configuration"
              class="w-full"
              @update:model-value="val => updateSchedule({ model_configuration: val })"
            />
          </div>

          <div v-if="schedule.retune">
            <label class="block text-sm font-medium text-neutral-700 mb-1">Split Config</label>
            <Select
              :model-value="schedule.split_config"
              :options="splitConfigOptions"
              option-label="label"
              option-value="value"
              placeholder="Select split config"
              class="w-full"
              @update:model-value="val => updateSchedule({ split_config: val })"
            />
          </div>

          <div v-if="schedule.retune">
            <label class="block text-sm font-medium text-neutral-700 mb-1">Evaluation Config</label>
            <Select
              :model-value="schedule.evaluation_config"
              :options="evalConfigOptions"
              option-label="label"
              option-value="value"
              placeholder="Select evaluation config"
              class="w-full"
              @update:model-value="val => updateSchedule({ evaluation_config: val })"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-neutral-700 mb-1">Auto Deploy</label>
            <InputSwitch
              :model-value="schedule.auto_deploy"
              @update:model-value="val => updateSchedule({ auto_deploy: val })"
            />
            <small class="text-neutral-400 mt-1 block">Deploy model after successful training</small>
          </div>

          <div>
            <label class="block text-sm font-medium text-neutral-700 mb-1">Max Retries</label>
            <InputText
              :model-value="String(schedule.max_retries)"
              type="number"
              class="w-full"
              @update:model-value="val => updateSchedule({ max_retries: Number(val) })"
            />
          </div>
        </div>

        <div class="flex items-center gap-4 mt-6 pt-4 border-t border-neutral-30">
          <div class="text-sm text-neutral-500">
            <span v-if="schedule.next_run_at">Next run: {{ formatDate(schedule.next_run_at) }}</span>
            <span v-else>No scheduled run</span>
          </div>
          <div
            v-if="schedule.last_run_at"
            class="text-sm text-neutral-500"
          >
            Last run: {{ formatDate(schedule.last_run_at) }}
            <Tag
              v-if="schedule.last_run_status"
              :value="schedule.last_run_status"
              :severity="runStatusSeverity(schedule.last_run_status)"
              class="ml-1"
            />
          </div>
          <div class="ml-auto">
            <Button
              label="Trigger Now"
              icon="pi pi-play"
              severity="warn"
              :loading="triggering"
              @click="handleTrigger"
            />
          </div>
        </div>
      </div>

      <!-- Recent Runs -->
      <h3 class="text-lg font-semibold text-neutral-800 mb-4">
        Recent Runs
      </h3>

      <div
        v-if="runsLoading"
        class="space-y-3"
      >
        <Skeleton
          v-for="i in 3"
          :key="i"
          height="3rem"
        />
      </div>

      <EmptyState
        v-else-if="runs.length === 0"
        icon="pi-history"
        title="No runs yet"
        description="Runs will appear here when the schedule triggers."
      />

      <DataTable
        v-else
        :value="runs"
        striped-rows
        paginator
        :rows="10"
      >
        <Column
          field="id"
          header="ID"
          sortable
          :style="{ width: '80px' }"
        />
        <Column header="Status">
          <template #body="{ data }">
            <Tag
              :value="data.status"
              :severity="runStatusSeverity(data.status)"
            />
          </template>
        </Column>
        <Column header="Trained Model">
          <template #body="{ data }">
            {{ data.trained_model ?? '-' }}
          </template>
        </Column>
        <Column
          field="ins_datetime"
          header="Started"
          sortable
        >
          <template #body="{ data }">
            {{ formatDate(data.ins_datetime) }}
          </template>
        </Column>
        <Column header="Completed">
          <template #body="{ data }">
            {{ data.completed_at ? formatDate(data.completed_at) : '-' }}
          </template>
        </Column>
        <Column header="Error">
          <template #body="{ data }">
            <span
              v-if="data.error_message"
              class="text-red-600 text-sm truncate max-w-xs block"
              :title="data.error_message"
            >{{ data.error_message }}</span>
            <span v-else>-</span>
          </template>
        </Column>
      </DataTable>
    </template>

    <!-- Create Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      header="Create Retraining Schedule"
      :modal="true"
      class="w-[520px]"
    >
      <div class="flex flex-col gap-4">
        <div>
          <label
            for="cron"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Cron Expression</label>
          <InputText
            id="cron"
            v-model="newForm.cron_expression"
            class="w-full"
            placeholder="0 2 * * *"
          />
          <small class="text-neutral-400 mt-1 block">e.g., "0 2 * * *" = daily at 2 AM</small>
        </div>
        <div class="flex items-center gap-2">
          <InputSwitch v-model="newForm.retune" />
          <span class="text-sm text-neutral-700">Retune parameters before training</span>
        </div>
        <div class="flex items-center gap-2">
          <InputSwitch v-model="newForm.auto_deploy" />
          <span class="text-sm text-neutral-700">Auto-deploy after training</span>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <Button
            label="Cancel"
            severity="secondary"
            @click="showCreateDialog = false"
          />
          <Button
            label="Create"
            :disabled="!newForm.cron_expression"
            :loading="creatingSchedule"
            @click="handleCreateSchedule"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import InputSwitch from "primevue/inputswitch";
import Select from "primevue/select";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { api, classifyApiError, unwrapResults } from "@/api/client";
import { ENDPOINTS } from "@/api/endpoints";
import EmptyState from "@/components/common/EmptyState.vue";
import type { ClassifiedApiError, TrainingData, ModelConfiguration, SplitConfig, EvaluationConfig } from "@/types";
import type { RetrainingSchedule, RetrainingRun } from "@/types/production";
import {
  getRetrainingSchedules,
  createRetrainingSchedule,
  updateRetrainingSchedule,
  triggerRetraining,
  getRetrainingRuns,
} from "@/api/production";

const route = useRoute();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = Number(route.params.projectId);

const schedule = ref<RetrainingSchedule | null>(null);
const runs = ref<RetrainingRun[]>([]);
const loading = ref(false);
const runsLoading = ref(false);
const error = ref<ClassifiedApiError | null>(null);
const triggering = ref(false);

const showCreateDialog = ref(false);
const creatingSchedule = ref(false);
const newForm = ref({
  cron_expression: "0 2 * * *",
  retune: false,
  auto_deploy: false,
});

const editForm = ref({
  cron_expression: "",
});

// Dropdown options
const trainingDataOptions = ref<{ label: string; value: number }[]>([]);
const modelConfigOptions = ref<{ label: string; value: number }[]>([]);
const splitConfigOptions = ref<{ label: string; value: number }[]>([]);
const evalConfigOptions = ref<{ label: string; value: number }[]>([]);

function runStatusSeverity(status: string): string {
  switch (status) {
    case "SUCCESS": return "success";
    case "FAILED": return "danger";
    case "RUNNING": return "info";
    default: return "secondary";
  }
}

async function fetchSchedule() {
  loading.value = true;
  error.value = null;
  try {
    const res = await getRetrainingSchedules(projectId, signal);
    const schedules = unwrapResults(res);
    schedule.value = schedules.length > 0 ? schedules[0] : null;
    if (schedule.value) {
      editForm.value.cron_expression = schedule.value.cron_expression;
      fetchRuns();
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

async function fetchRuns() {
  if (!schedule.value) return;
  runsLoading.value = true;
  try {
    const res = await getRetrainingRuns(schedule.value.id, signal);
    runs.value = unwrapResults(res);
  } catch {
    // Silently handle run fetch errors
  } finally {
    runsLoading.value = false;
  }
}

async function fetchDropdownData() {
  try {
    const [tdRes, mcRes, scRes, ecRes] = await Promise.all([
      api<{ results: TrainingData[] }>(ENDPOINTS.TRAINING_DATA, { params: { project: projectId }, signal }),
      api<{ results: ModelConfiguration[] }>(ENDPOINTS.MODEL_CONFIGURATION, { params: { project: projectId }, signal }),
      api<{ results: SplitConfig[] }>(ENDPOINTS.SPLIT_CONFIG, { signal }),
      api<{ results: EvaluationConfig[] }>(ENDPOINTS.EVALUATION_CONFIG, { signal }),
    ]);
    trainingDataOptions.value = unwrapResults(tdRes).map(d => ({ label: d.basename || `Data #${d.id}`, value: d.id }));
    modelConfigOptions.value = unwrapResults(mcRes).map(c => ({ label: c.name || c.recommender_class_name, value: c.id }));
    splitConfigOptions.value = unwrapResults(scRes).map(s => ({ label: s.name || `Split #${s.id}`, value: s.id }));
    evalConfigOptions.value = unwrapResults(ecRes).map(e => ({ label: e.name || `Eval #${e.id}`, value: e.id }));
  } catch {
    // Options will remain empty, user can still type IDs
  }
}

onMounted(() => {
  fetchSchedule();
  fetchDropdownData();
});

async function updateSchedule(fields: Partial<RetrainingSchedule>) {
  if (!schedule.value) return;
  try {
    schedule.value = await updateRetrainingSchedule(schedule.value.id, fields);
    editForm.value.cron_expression = schedule.value.cron_expression;
    notify.success("Schedule updated.");
  } catch {
    notify.error("Failed to update schedule.");
  }
}

async function saveField(field: keyof typeof editForm.value) {
  await updateSchedule({ [field]: editForm.value[field] } as Partial<RetrainingSchedule>);
}

async function toggleEnabled(val: boolean) {
  await updateSchedule({ is_enabled: val });
}

async function handleTrigger() {
  if (!schedule.value) return;
  triggering.value = true;
  try {
    await triggerRetraining(schedule.value.id);
    notify.success("Retraining triggered.");
    await fetchRuns();
  } catch {
    notify.error("Failed to trigger retraining.");
  } finally {
    triggering.value = false;
  }
}

async function handleCreateSchedule() {
  creatingSchedule.value = true;
  try {
    schedule.value = await createRetrainingSchedule({
      project: projectId,
      cron_expression: newForm.value.cron_expression,
      retune: newForm.value.retune,
      auto_deploy: newForm.value.auto_deploy,
      is_enabled: true,
    });
    editForm.value.cron_expression = schedule.value.cron_expression;
    showCreateDialog.value = false;
    notify.success("Schedule created.");
  } catch {
    notify.error("Failed to create schedule.");
  } finally {
    creatingSchedule.value = false;
  }
}
</script>
