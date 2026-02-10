<template>
  <router-link
    :to="to"
    :class="[
      'flex items-center mx-2 px-3 py-2 rounded-md text-sm transition-colors',
      isActive
        ? 'bg-primary/10 text-primary font-medium'
        : 'text-neutral-500 hover:bg-neutral-30'
    ]"
    :title="collapsed ? label : undefined"
  >
    <i :class="['pi', icon, 'text-base']" />
    <span
      v-show="!collapsed"
      class="ml-3 truncate"
    >{{ label }}</span>
  </router-link>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

const props = defineProps<{
  to: string;
  icon: string;
  label: string;
  collapsed: boolean;
}>();

const route = useRoute();
const isActive = computed(() => route.path === props.to || route.path.startsWith(props.to + "/"));
</script>
