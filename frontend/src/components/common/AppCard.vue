<template>
  <div
    class="bg-white rounded-lg shadow-sm border border-neutral-30"
    :class="$attrs.class"
  >
    <div
      v-if="title || $slots.header"
      class="px-6 py-4 border-b border-neutral-30"
    >
      <slot name="header">
        <h3 class="text-base font-semibold text-neutral-800">
          {{ title }}
        </h3>
      </slot>
    </div>
    <div class="p-6">
      <slot />
    </div>
    <div
      v-if="$slots.footer"
      class="px-6 py-4 border-t border-neutral-30 bg-neutral-10"
    >
      <slot name="footer" />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title?: string;
}>();
</script>
