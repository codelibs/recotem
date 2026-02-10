<template>
  <router-link
    :to="to"
    class="block bg-white rounded-lg shadow-sm border border-neutral-30 p-5 hover:shadow-md transition-shadow"
  >
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-md bg-primary/10 flex items-center justify-center">
        <i :class="['pi', icon, 'text-primary']" />
      </div>
      <div>
        <div class="text-2xl font-bold text-neutral-800">
          {{ value }}
        </div>
        <div class="text-sm text-neutral-200">
          {{ label }}
        </div>
      </div>
    </div>
  </router-link>
</template>

<script setup lang="ts">
defineProps<{
  icon: string;
  label: string;
  value: number | string;
  to: string;
}>();
</script>
