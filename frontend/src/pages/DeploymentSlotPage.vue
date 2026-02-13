<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        Deployment Slots
      </h2>
      <Button
        label="New Slot"
        icon="pi pi-plus"
        @click="openCreateDialog"
      />
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>{{ error.message ?? "Failed to load deployment slots." }}</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchSlots"
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
      v-else-if="!error && slots.length === 0"
      icon="pi-server"
      title="No deployment slots"
      description="Create deployment slots to serve trained models."
    >
      <Button
        label="New Slot"
        icon="pi pi-plus"
        @click="openCreateDialog"
      />
    </EmptyState>

    <div
      v-else
      class="overflow-x-auto"
    >
      <!-- Weight summary -->
      <div class="mb-4 p-3 bg-neutral-20 rounded text-sm text-neutral-600">
        Total weight: <strong>{{ totalWeight }}</strong>
        <span v-if="totalWeight > 0 && slots.some(s => s.is_active)">
          — Traffic split:
          <span
            v-for="slot in activeSlots"
            :key="slot.id"
            class="inline-block ml-2"
          >
            {{ slot.name }}: {{ ((slot.weight / totalWeight) * 100).toFixed(1) }}%
          </span>
        </span>
      </div>

      <DataTable
        :value="slots"
        striped-rows
        paginator
        :rows="20"
      >
        <Column
          field="name"
          header="Name"
          sortable
        />
        <Column
          field="trained_model"
          header="Model"
          sortable
        >
          <template #body="{ data }">
            Model #{{ data.trained_model }}
          </template>
        </Column>
        <Column
          header="Weight"
          sortable
          field="weight"
        >
          <template #body="{ data }">
            <div class="flex items-center gap-2">
              <InputText
                :model-value="String(data.weight)"
                type="number"
                class="w-20"
                :min="0"
                :max="100"
                @update:model-value="val => handleWeightChange(data, Number(val))"
              />
            </div>
          </template>
        </Column>
        <Column header="Status">
          <template #body="{ data }">
            <InputSwitch
              :model-value="data.is_active"
              @update:model-value="val => handleToggleActive(data, val)"
            />
          </template>
        </Column>
        <Column
          field="updated_at"
          header="Updated"
          sortable
        >
          <template #body="{ data }">
            {{ formatDate(data.updated_at) }}
          </template>
        </Column>
        <Column
          header="Actions"
          :style="{ width: '80px' }"
        >
          <template #body="{ data }">
            <Button
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              aria-label="Delete slot"
              @click="confirmDelete(data)"
            />
          </template>
        </Column>
      </DataTable>
    </div>

    <!-- Create Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      header="New Deployment Slot"
      :modal="true"
      class="w-[480px]"
    >
      <div class="flex flex-col gap-4">
        <div>
          <label
            for="slot-name"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Name</label>
          <InputText
            id="slot-name"
            v-model="createForm.name"
            class="w-full"
            placeholder="e.g., Primary, Canary"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-neutral-700 mb-1">Trained Model</label>
          <Select
            v-model="createForm.trained_model"
            :options="modelOptions"
            option-label="label"
            option-value="value"
            placeholder="Select a trained model"
            class="w-full"
          />
        </div>
        <div>
          <label
            for="slot-weight"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Weight</label>
          <InputText
            id="slot-weight"
            :model-value="String(createForm.weight)"
            type="number"
            class="w-full"
            :min="0"
            :max="100"
            @update:model-value="val => createForm.weight = Number(val)"
          />
          <small class="text-neutral-400 mt-1 block">Traffic proportion relative to other active slots</small>
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
            :disabled="!createForm.name || !createForm.trained_model"
            :loading="creating"
            @click="handleCreate"
          />
        </div>
      </template>
    </Dialog>

    <!-- Delete Confirmation -->
    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      header="Delete Deployment Slot"
      :message="`Are you sure you want to delete '${deleteTarget?.name}'?`"
      confirm-label="Delete"
      cancel-label="Cancel"
      danger
      @confirm="executeDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRoute } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import InputSwitch from "primevue/inputswitch";
import Select from "primevue/select";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { api, classifyApiError, unwrapResults } from "@/api/client";
import { ENDPOINTS } from "@/api/endpoints";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import type { ClassifiedApiError, TrainedModel } from "@/types";
import type { DeploymentSlot } from "@/types/production";
import {
  getDeploymentSlots,
  createDeploymentSlot,
  updateDeploymentSlot,
  deleteDeploymentSlot,
} from "@/api/production";

const route = useRoute();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = Number(route.params.projectId);

const slots = ref<DeploymentSlot[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);

const showCreateDialog = ref(false);
const creating = ref(false);
const createForm = ref({
  name: "",
  trained_model: null as number | null,
  weight: 50,
});

const showDeleteConfirm = ref(false);
const deleteTarget = ref<DeploymentSlot | null>(null);

const modelOptions = ref<{ label: string; value: number }[]>([]);

const activeSlots = computed(() => slots.value.filter(s => s.is_active));
const totalWeight = computed(() => activeSlots.value.reduce((sum, s) => sum + s.weight, 0));

async function fetchSlots() {
  loading.value = true;
  error.value = null;
  try {
    const res = await getDeploymentSlots(projectId, signal);
    slots.value = unwrapResults(res);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

async function fetchModels() {
  try {
    const res = await api<{ results: TrainedModel[] }>(ENDPOINTS.TRAINED_MODEL, {
      params: { data_loc__project: projectId },
      signal,
    });
    modelOptions.value = unwrapResults(res).map(m => ({
      label: m.basename || `Model #${m.id}`,
      value: m.id,
    }));
  } catch {
    // Options will remain empty
  }
}

onMounted(() => {
  fetchSlots();
  fetchModels();
});

function openCreateDialog() {
  createForm.value = { name: "", trained_model: null, weight: 50 };
  showCreateDialog.value = true;
}

async function handleCreate() {
  if (!createForm.value.trained_model) return;
  creating.value = true;
  try {
    const newSlot = await createDeploymentSlot({
      project: projectId,
      name: createForm.value.name,
      trained_model: createForm.value.trained_model,
      weight: createForm.value.weight,
    });
    slots.value.push(newSlot);
    showCreateDialog.value = false;
    notify.success("Deployment slot created.");
  } catch {
    notify.error("Failed to create deployment slot.");
  } finally {
    creating.value = false;
  }
}

async function handleWeightChange(slot: DeploymentSlot, weight: number) {
  try {
    const updated = await updateDeploymentSlot(slot.id, { weight });
    const idx = slots.value.findIndex(s => s.id === slot.id);
    if (idx !== -1) slots.value[idx] = updated;
  } catch {
    notify.error("Failed to update weight.");
  }
}

async function handleToggleActive(slot: DeploymentSlot, isActive: boolean) {
  try {
    const updated = await updateDeploymentSlot(slot.id, { is_active: isActive });
    const idx = slots.value.findIndex(s => s.id === slot.id);
    if (idx !== -1) slots.value[idx] = updated;
  } catch {
    notify.error("Failed to update slot status.");
  }
}

function confirmDelete(slot: DeploymentSlot) {
  deleteTarget.value = slot;
  showDeleteConfirm.value = true;
}

async function executeDelete() {
  if (!deleteTarget.value) return;
  try {
    await deleteDeploymentSlot(deleteTarget.value.id);
    slots.value = slots.value.filter(s => s.id !== deleteTarget.value!.id);
    notify.success("Deployment slot deleted.");
  } catch {
    notify.error("Failed to delete deployment slot.");
  }
  deleteTarget.value = null;
}
</script>
