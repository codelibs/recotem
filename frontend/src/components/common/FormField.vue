<template>
  <div class="mb-4">
    <label
      v-if="label"
      :for="fieldId"
      class="block text-sm font-medium text-neutral-500 mb-1"
    >
      {{ label }}
      <span
        v-if="required"
        class="text-danger"
        aria-hidden="true"
      >*</span>
      <i
        v-if="tooltip"
        v-tooltip.top="tooltip"
        class="pi pi-info-circle text-neutral-100 ml-1 cursor-help"
        style="font-size: 0.75rem"
      />
    </label>
    <slot
      :id="fieldId"
      :has-error="!!error"
    />
    <p
      v-if="error"
      :id="`${fieldId}-error`"
      class="mt-1 text-sm text-danger"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-else-if="hint"
      :id="`${fieldId}-hint`"
      class="mt-1 text-sm text-neutral-100"
    >
      {{ hint }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  label?: string;
  error?: string;
  hint?: string;
  tooltip?: string;
  required?: boolean;
  name?: string;
}>();

const fieldId = computed(() => `field-${props.name ?? props.label?.toLowerCase().replace(/\s+/g, "-") ?? "input"}`);
</script>
