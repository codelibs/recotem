<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        A/B Tests
      </h2>
      <Button
        label="New Test"
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
        <span>{{ error.message ?? "Failed to load A/B tests." }}</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchTests"
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
      v-else-if="!error && tests.length === 0"
      icon="pi-chart-line"
      title="No A/B tests"
      description="Create an A/B test to compare deployment slots."
    >
      <Button
        label="New Test"
        icon="pi pi-plus"
        @click="showCreateDialog = true"
      />
    </EmptyState>

    <div
      v-else
      class="overflow-x-auto"
    >
      <DataTable
        :value="tests"
        striped-rows
        paginator
        :rows="20"
      >
        <Column header="Name">
          <template #body="{ data }">
            <router-link
              :to="`/projects/${projectId}/ab-tests/${data.id}`"
              class="text-primary hover:underline"
            >
              {{ data.name }}
            </router-link>
          </template>
        </Column>
        <Column header="Status">
          <template #body="{ data }">
            <Tag
              :value="data.status"
              :severity="statusSeverity(data.status)"
            />
          </template>
        </Column>
        <Column
          field="control_slot"
          header="Control"
        >
          <template #body="{ data }">
            Slot #{{ data.control_slot }}
          </template>
        </Column>
        <Column
          field="variant_slot"
          header="Variant"
        >
          <template #body="{ data }">
            Slot #{{ data.variant_slot }}
          </template>
        </Column>
        <Column
          field="target_metric_name"
          header="Metric"
        />
        <Column
          field="started_at"
          header="Started"
          sortable
        >
          <template #body="{ data }">
            {{ data.started_at ? formatDate(data.started_at) : '-' }}
          </template>
        </Column>
        <Column
          header="Actions"
          :style="{ width: '100px' }"
        >
          <template #body="{ data }">
            <Button
              icon="pi pi-eye"
              text
              rounded
              :aria-label="`View test ${data.name}`"
              @click="router.push(`/projects/${projectId}/ab-tests/${data.id}`)"
            />
          </template>
        </Column>
      </DataTable>
    </div>

    <!-- Create Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      header="New A/B Test"
      :modal="true"
      class="w-[520px]"
    >
      <div class="flex flex-col gap-4">
        <div>
          <label
            for="test-name"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Name</label>
          <InputText
            id="test-name"
            v-model="createForm.name"
            class="w-full"
            placeholder="e.g., Algorithm comparison Q1"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-neutral-700 mb-1">Control Slot</label>
          <Select
            v-model="createForm.control_slot"
            :options="slotOptions"
            option-label="label"
            option-value="value"
            placeholder="Select control slot"
            class="w-full"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-neutral-700 mb-1">Variant Slot</label>
          <Select
            v-model="createForm.variant_slot"
            :options="slotOptions"
            option-label="label"
            option-value="value"
            placeholder="Select variant slot"
            class="w-full"
          />
        </div>
        <div>
          <label
            for="metric"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Target Metric</label>
          <InputText
            id="metric"
            v-model="createForm.target_metric_name"
            class="w-full"
            placeholder="e.g., click_through_rate"
          />
        </div>
        <div>
          <label
            for="min-sample"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Min Sample Size</label>
          <InputText
            id="min-sample"
            :model-value="String(createForm.min_sample_size)"
            type="number"
            class="w-full"
            :min="100"
            @update:model-value="val => createForm.min_sample_size = Number(val)"
          />
        </div>
        <div>
          <label
            for="confidence"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Confidence Level</label>
          <InputText
            id="confidence"
            :model-value="String(createForm.confidence_level)"
            type="number"
            class="w-full"
            :min="0.8"
            :max="0.99"
            step="0.01"
            @update:model-value="val => createForm.confidence_level = Number(val)"
          />
          <small class="text-neutral-400 mt-1 block">e.g., 0.95 for 95% confidence</small>
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
            :disabled="!createForm.name || !createForm.control_slot || !createForm.variant_slot"
            :loading="creating"
            @click="handleCreate"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import Select from "primevue/select";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { classifyApiError, unwrapResults } from "@/api/client";
import EmptyState from "@/components/common/EmptyState.vue";
import type { ClassifiedApiError } from "@/types";
import type { ABTest, DeploymentSlot } from "@/types/production";
import { getABTests, createABTest, getDeploymentSlots } from "@/api/production";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = Number(route.params.projectId);

const tests = ref<ABTest[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);

const showCreateDialog = ref(false);
const creating = ref(false);
const createForm = ref({
  name: "",
  control_slot: null as number | null,
  variant_slot: null as number | null,
  target_metric_name: "click_through_rate",
  min_sample_size: 1000,
  confidence_level: 0.95,
});

const slotOptions = ref<{ label: string; value: number }[]>([]);

function statusSeverity(status: ABTest["status"]): string {
  switch (status) {
    case "DRAFT": return "info";
    case "RUNNING": return "warn";
    case "COMPLETED": return "success";
    case "CANCELLED": return "danger";
    default: return "secondary";
  }
}

async function fetchTests() {
  loading.value = true;
  error.value = null;
  try {
    const res = await getABTests(projectId, signal);
    tests.value = unwrapResults(res);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

async function fetchSlots() {
  try {
    const res = await getDeploymentSlots(projectId, signal);
    slotOptions.value = unwrapResults(res).map((s: DeploymentSlot) => ({
      label: s.name,
      value: s.id,
    }));
  } catch {
    // Options will remain empty
  }
}

onMounted(() => {
  fetchTests();
  fetchSlots();
});

async function handleCreate() {
  if (!createForm.value.control_slot || !createForm.value.variant_slot) return;
  creating.value = true;
  try {
    await createABTest({
      project: projectId,
      name: createForm.value.name,
      control_slot: createForm.value.control_slot,
      variant_slot: createForm.value.variant_slot,
      target_metric_name: createForm.value.target_metric_name,
      min_sample_size: createForm.value.min_sample_size,
      confidence_level: createForm.value.confidence_level,
    });
    showCreateDialog.value = false;
    notify.success("A/B test created.");
    await fetchTests();
  } catch {
    notify.error("Failed to create A/B test.");
  } finally {
    creating.value = false;
  }
}
</script>
