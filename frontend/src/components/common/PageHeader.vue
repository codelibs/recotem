<template>
  <div class="flex items-center justify-between mb-6">
    <div class="flex items-center gap-3">
      <Button
        v-if="backTo"
        icon="pi pi-arrow-left"
        text
        rounded
        @click="$router.push(backTo)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        <slot>{{ title }}</slot>
      </h2>
      <slot name="badge" />
    </div>
    <div class="flex items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
import Button from "primevue/button";

defineProps<{
  title?: string;
  backTo?: string;
}>();
</script>
