<template>
  <div class="text-center py-12">
    <i :class="['pi', icon, 'text-5xl text-neutral-40 mb-4']" />
    <h3 class="text-lg font-medium text-neutral-500">
      {{ title }}
    </h3>
    <p
      v-if="description"
      class="text-neutral-200 mt-1 mb-4"
    >
      {{ description }}
    </p>
    <slot />
  </div>
</template>

<script setup lang="ts">
defineProps<{
  icon: string;
  title: string;
  description?: string;
}>();
</script>
