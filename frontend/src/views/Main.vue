<template>
  <router-view> </router-view>
</template>

<script lang="ts">
import Vue from "vue";
import { guard } from "@/router/guard.ts";

export default Vue.extend({
  beforeRouteEnter: guard,
  beforeRouteUpdate: guard,
});
</script>
