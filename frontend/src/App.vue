<template>
  <div
    v-if="loading"
    class="route-loading-bar"
    :style="{ width: loadingProgress + '%' }"
  />
  <div
    v-if="hasError"
    class="min-h-screen flex items-center justify-center bg-neutral-10"
  >
    <div class="text-center p-8 max-w-md">
      <i class="pi pi-exclamation-triangle text-5xl text-orange-500 mb-4" />
      <h2 class="text-xl font-semibold text-neutral-800 mb-2">
        Something went wrong
      </h2>
      <p class="text-neutral-200 mb-6">
        An unexpected error occurred. Please try refreshing the page.
      </p>
      <Button
        label="Refresh Page"
        icon="pi pi-refresh"
        @click="handleRefresh"
      />
    </div>
  </div>
  <template v-else>
    <router-view />
  </template>
  <Toast position="top-right" />
</template>

<script setup lang="ts">
import { ref, onUnmounted, onErrorCaptured } from "vue";
import Toast from "primevue/toast";
import Button from "primevue/button";
import router from "./router";

const loading = ref(false);
const loadingProgress = ref(0);
const hasError = ref(false);
let loadingTimer: ReturnType<typeof setInterval> | null = null;

onErrorCaptured((err) => {
  if (import.meta.env.DEV) console.error("Uncaught error:", err);
  hasError.value = true;
  return false;
});

function handleRefresh() {
  window.location.reload();
}

function startLoading() {
  loading.value = true;
  loadingProgress.value = 20;
  loadingTimer = setInterval(() => {
    if (loadingProgress.value < 90) {
      loadingProgress.value += 10;
    }
  }, 150);
}

function stopLoading() {
  if (loadingTimer) {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }
  loadingProgress.value = 100;
  setTimeout(() => {
    loading.value = false;
    loadingProgress.value = 0;
  }, 200);
}

const removeBeforeEach = router.beforeEach((_to, _from, next) => {
  startLoading();
  next();
});

const removeAfterEach = router.afterEach(() => {
  stopLoading();
});

onUnmounted(() => {
  removeBeforeEach();
  removeAfterEach();
  if (loadingTimer) clearInterval(loadingTimer);
});
</script>
