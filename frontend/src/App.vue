<template>
  <v-app>
    <v-app-bar app toolbar v-if="$route.name !== 'login'" flat>
      <router-link :to="{ name: 'project-list' }">
        <v-img
          src="@/assets/logo.png"
          max-height="32"
          max-width="32"
          contain
          class="mx-4"
        >
        </v-img>
      </router-link>
      <v-toolbar-title>Recotem</v-toolbar-title>
      <v-spacer></v-spacer>
      <CurrentUser />
    </v-app-bar>

    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>
<script lang="ts">
import Vue from "vue";
import CurrentUser from "@/components/CurrentUser.vue";
export default Vue.extend({
  components: {
    CurrentUser,
  },
});
</script>
