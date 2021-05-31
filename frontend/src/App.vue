<template>
  <v-app>
    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>

<script lang="ts">
import Vue from "vue";

import { AuthModule } from "@/store/auth";

export default Vue.extend({
  name: "App",

  data: () => ({
    //
  }),
  computed: {
    username() {
      return AuthModule.username;
    },
  },
  watch: {},
});
</script>
