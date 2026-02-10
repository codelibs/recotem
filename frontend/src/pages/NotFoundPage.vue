<template>
  <div class="flex flex-col items-center justify-center min-h-[60vh] text-center">
    <i class="pi pi-exclamation-triangle text-6xl text-neutral-100 mb-4" />
    <h1 class="text-2xl font-bold text-neutral-800 mb-2">
      {{ t('errors.pageNotFound') }}
    </h1>
    <p class="text-neutral-200 mb-6 max-w-md">
      {{ t('errors.pageNotFoundMessage') }}
    </p>
    <Button
      :label="t('errors.goHome')"
      icon="pi pi-arrow-left"
      @click="router.push('/projects')"
    />
  </div>
</template>

<script setup lang="ts">
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import Button from "primevue/button";

const { t } = useI18n();
const router = useRouter();
</script>
