<template>
  <div>
    <div class="flex items-center gap-2 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        aria-label="Back to A/B tests"
        @click="router.push(`/projects/${projectId}/ab-tests`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        {{ test?.name ?? 'A/B Test Detail' }}
      </h2>
      <Tag
        v-if="test"
        :value="test.status"
        :severity="statusSeverity(test.status)"
        class="ml-2"
      />
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>{{ error.message ?? "Failed to load test details." }}</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchTest"
        />
      </div>
    </Message>

    <div
      v-if="loading"
      class="space-y-3"
    >
      <Skeleton
        v-for="i in 4"
        :key="i"
        height="3rem"
      />
    </div>

    <template v-else-if="test">
      <!-- Test Info -->
      <div class="bg-white border border-neutral-30 rounded-lg p-6 mb-6">
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <span class="text-sm text-neutral-500 block">Control Slot</span>
            <span class="font-medium">Slot #{{ test.control_slot }}</span>
          </div>
          <div>
            <span class="text-sm text-neutral-500 block">Variant Slot</span>
            <span class="font-medium">Slot #{{ test.variant_slot }}</span>
          </div>
          <div>
            <span class="text-sm text-neutral-500 block">Target Metric</span>
            <span class="font-medium">{{ test.target_metric_name }}</span>
          </div>
          <div>
            <span class="text-sm text-neutral-500 block">Confidence Level</span>
            <span class="font-medium">{{ (test.confidence_level * 100).toFixed(0) }}%</span>
          </div>
          <div>
            <span class="text-sm text-neutral-500 block">Min Sample Size</span>
            <span class="font-medium">{{ test.min_sample_size.toLocaleString() }}</span>
          </div>
          <div>
            <span class="text-sm text-neutral-500 block">Started</span>
            <span class="font-medium">{{ test.started_at ? formatDate(test.started_at) : 'Not started' }}</span>
          </div>
          <div>
            <span class="text-sm text-neutral-500 block">Ended</span>
            <span class="font-medium">{{ test.ended_at ? formatDate(test.ended_at) : '-' }}</span>
          </div>
          <div>
            <span class="text-sm text-neutral-500 block">Created</span>
            <span class="font-medium">{{ formatDate(test.ins_datetime) }}</span>
          </div>
        </div>

        <!-- Action buttons -->
        <div class="flex gap-2 mt-6 pt-4 border-t border-neutral-30">
          <Button
            v-if="test.status === 'DRAFT'"
            label="Start Test"
            icon="pi pi-play"
            :loading="actionLoading"
            @click="handleStart"
          />
          <Button
            v-if="test.status === 'RUNNING'"
            label="Stop Test"
            icon="pi pi-stop"
            severity="warn"
            :loading="actionLoading"
            @click="handleStop"
          />
          <Button
            v-if="test.status === 'COMPLETED' && results && results.significant && !test.winner_slot"
            label="Promote Winner"
            icon="pi pi-trophy"
            severity="success"
            :loading="actionLoading"
            @click="handlePromote"
          />
          <Tag
            v-if="test.winner_slot"
            :value="`Winner: Slot #${test.winner_slot}`"
            severity="success"
            class="ml-2"
          />
        </div>
      </div>

      <!-- Results -->
      <h3 class="text-lg font-semibold text-neutral-800 mb-4">
        Results
      </h3>

      <div
        v-if="resultsLoading"
        class="space-y-3"
      >
        <Skeleton
          v-for="i in 3"
          :key="i"
          height="3rem"
        />
      </div>

      <EmptyState
        v-else-if="!results"
        icon="pi-chart-bar"
        title="No results yet"
        description="Results will appear after the test starts collecting data."
      />

      <template v-else>
        <!-- Significance indicator -->
        <div class="mb-4">
          <Tag
            :value="results.significant ? 'Statistically Significant' : 'Not Significant'"
            :severity="results.significant ? 'success' : 'secondary'"
            class="text-base px-3 py-1"
          />
        </div>

        <!-- Results grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
          <!-- Control -->
          <div class="bg-white border border-neutral-30 rounded-lg p-6">
            <h4 class="text-base font-semibold text-neutral-700 mb-4">
              Control (Slot #{{ test.control_slot }})
            </h4>
            <div class="space-y-3">
              <div class="flex justify-between">
                <span class="text-neutral-500">Impressions</span>
                <span class="font-medium">{{ results.control_impressions.toLocaleString() }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-neutral-500">Conversions</span>
                <span class="font-medium">{{ results.control_conversions.toLocaleString() }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-neutral-500">Rate</span>
                <span class="font-medium">{{ (results.control_rate * 100).toFixed(4) }}%</span>
              </div>
            </div>
          </div>

          <!-- Variant -->
          <div class="bg-white border border-neutral-30 rounded-lg p-6">
            <h4 class="text-base font-semibold text-neutral-700 mb-4">
              Variant (Slot #{{ test.variant_slot }})
            </h4>
            <div class="space-y-3">
              <div class="flex justify-between">
                <span class="text-neutral-500">Impressions</span>
                <span class="font-medium">{{ results.variant_impressions.toLocaleString() }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-neutral-500">Conversions</span>
                <span class="font-medium">{{ results.variant_conversions.toLocaleString() }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-neutral-500">Rate</span>
                <span class="font-medium">{{ (results.variant_rate * 100).toFixed(4) }}%</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Statistical Summary -->
        <div class="bg-white border border-neutral-30 rounded-lg p-6">
          <h4 class="text-base font-semibold text-neutral-700 mb-4">
            Statistical Summary
          </h4>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <span class="text-sm text-neutral-500 block">p-value</span>
              <span class="font-medium text-lg">{{ results.p_value.toFixed(6) }}</span>
            </div>
            <div>
              <span class="text-sm text-neutral-500 block">z-score</span>
              <span class="font-medium text-lg">{{ results.z_score.toFixed(4) }}</span>
            </div>
            <div>
              <span class="text-sm text-neutral-500 block">Lift</span>
              <span
                class="font-medium text-lg"
                :class="results.lift > 0 ? 'text-green-600' : results.lift < 0 ? 'text-red-600' : ''"
              >{{ results.lift > 0 ? '+' : '' }}{{ (results.lift * 100).toFixed(2) }}%</span>
            </div>
            <div>
              <span class="text-sm text-neutral-500 block">Confidence Interval</span>
              <span class="font-medium text-lg">
                [{{ (results.confidence_interval[0] * 100).toFixed(2) }}%,
                {{ (results.confidence_interval[1] * 100).toFixed(2) }}%]
              </span>
            </div>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { classifyApiError } from "@/api/client";
import EmptyState from "@/components/common/EmptyState.vue";
import type { ClassifiedApiError } from "@/types";
import type { ABTest, ABTestResult } from "@/types/production";
import {
  getABTestDetail,
  getABTestResults,
  startABTest,
  stopABTest,
  promoteWinner,
} from "@/api/production";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = route.params.projectId as string;
const testId = Number(route.params.testId);

const test = ref<ABTest | null>(null);
const results = ref<ABTestResult | null>(null);
const loading = ref(false);
const resultsLoading = ref(false);
const error = ref<ClassifiedApiError | null>(null);
const actionLoading = ref(false);

function statusSeverity(status: ABTest["status"]): string {
  switch (status) {
    case "DRAFT": return "info";
    case "RUNNING": return "warn";
    case "COMPLETED": return "success";
    case "CANCELLED": return "danger";
    default: return "secondary";
  }
}

async function fetchTest() {
  loading.value = true;
  error.value = null;
  try {
    test.value = await getABTestDetail(testId, signal);
    fetchResults();
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

async function fetchResults() {
  if (!test.value || test.value.status === "DRAFT") return;
  resultsLoading.value = true;
  try {
    results.value = await getABTestResults(testId, signal);
  } catch {
    // Results may not be available yet
    results.value = null;
  } finally {
    resultsLoading.value = false;
  }
}

// Auto-refresh when test is RUNNING
let pollTimer: ReturnType<typeof setInterval> | null = null;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    if (test.value?.status === "RUNNING") {
      try {
        test.value = await getABTestDetail(testId, signal);
        const res = await getABTestResults(testId, signal);
        results.value = res;
      } catch {
        // Ignore poll errors
      }
    } else {
      stopPolling();
    }
  }, 10000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onMounted(() => {
  fetchTest().then(() => {
    if (test.value?.status === "RUNNING") {
      startPolling();
    }
  });
});

onUnmounted(stopPolling);

async function handleStart() {
  actionLoading.value = true;
  try {
    test.value = await startABTest(testId);
    notify.success("A/B test started.");
    startPolling();
  } catch {
    notify.error("Failed to start A/B test.");
  } finally {
    actionLoading.value = false;
  }
}

async function handleStop() {
  actionLoading.value = true;
  try {
    test.value = await stopABTest(testId);
    notify.success("A/B test stopped.");
    stopPolling();
    await fetchResults();
  } catch {
    notify.error("Failed to stop A/B test.");
  } finally {
    actionLoading.value = false;
  }
}

async function handlePromote() {
  if (!test.value || !results.value) return;
  // Determine the winner based on variant_rate vs control_rate
  const winnerSlot = results.value.variant_rate > results.value.control_rate
    ? test.value.variant_slot
    : test.value.control_slot;
  actionLoading.value = true;
  try {
    await promoteWinner(testId, winnerSlot);
    notify.success("Winner promoted.");
    await fetchTest();
  } catch {
    notify.error("Failed to promote winner.");
  } finally {
    actionLoading.value = false;
  }
}
</script>
