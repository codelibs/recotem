<template>
  <div class="flex flex-col items-center justify-center min-h-[60vh] text-center">
    <i class="pi pi-exclamation-triangle text-6xl text-neutral-100 mb-4" />
    <h1 class="text-2xl font-bold text-neutral-800 mb-2">
      Page Not Found
    </h1>
    <p class="text-neutral-200 mb-6 max-w-md">
      The page you're looking for doesn't exist or has been moved.
    </p>
    <Button
      label="Go to Projects"
      icon="pi pi-arrow-left"
      @click="router.push('/projects')"
    />
  </div>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";
import Button from "primevue/button";

const router = useRouter();
</script>
