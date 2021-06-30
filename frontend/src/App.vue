<style scoped></style>
<template>
  <v-app>
    <v-app-bar app toolbar v-if="$route.name !== 'login'" flat clipped-left>
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
      <v-toolbar-title>
        Recotem
        <span class="text-caption"> ver {{ version }} </span></v-toolbar-title
      >
      <v-spacer></v-spacer>
      <CurrentUser />
    </v-app-bar>
    <v-navigation-drawer
      app
      v-if="project !== null"
      expand-on-hover
      permanent
      clipped
    >
      <v-list nav flat>
        <v-list-item
          link
          :to="{ name: 'project', params: { projectId: project.id } }"
        >
          <v-list-item-icon>
            <v-icon color="primary"> mdi-home</v-icon>
          </v-list-item-icon>
          <v-list-item-title>
            <span class="text-caption"> Project </span><br />
            {{ project.name }}
          </v-list-item-title>
        </v-list-item>
      </v-list>
      <v-list nav>
        <v-divider></v-divider>
        <v-list-item
          :to="{ name: 'data-list', params: { projectId: project.id } }"
          link
          :ripple="false"
        >
          <v-list-item-icon>
            <v-icon> mdi-folder</v-icon>
          </v-list-item-icon>
          <v-list-item-title> Data </v-list-item-title>
        </v-list-item>
        <v-list-item
          :to="{ name: 'tuning-job-list', params: { projectId: project.id } }"
          link
          :ripple="false"
        >
          <v-list-item-icon>
            <v-icon> mdi-tune</v-icon>
          </v-list-item-icon>
          <v-list-item-title> Tuning </v-list-item-title>
        </v-list-item>
        <v-list-item
          link
          :ripple="false"
          :to="{
            name: 'trained-model-list',
            params: { projectId: project.id },
          }"
        >
          <v-list-item-icon>
            <v-icon> mdi-calculator</v-icon>
          </v-list-item-icon>
          <v-list-item-title> Models </v-list-item-title>
        </v-list-item>
        <v-spacer> </v-spacer>
      </v-list>
    </v-navigation-drawer>

    <v-main>
      <router-view />
    </v-main>
    <v-footer v-if="errors.length > 0" app class="text-center">
      <template v-for="(em, i) in errors">
        <v-alert :key="i" type="error">
          {{ em }}
          <v-icon>mdi-dismiss</v-icon>
        </v-alert>
      </template>
    </v-footer>
  </v-app>
</template>
<script lang="ts">
import Vue from "vue";
import CurrentUser from "@/components/CurrentUser.vue";
import { AuthModule } from "./store/auth";
export default Vue.extend({
  computed: {
    version: () => AuthModule.recotemVersion,
    project: () => AuthModule.currentProjectDetail,
    errors: () => AuthModule.errors,
  },
  components: {
    CurrentUser,
  },
});
</script>
