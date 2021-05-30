<template>
  <v-card>
    <v-toolbar color="primary" dark flat>
      <v-toolbar-title>Recotem Project Manager</v-toolbar-title>

      <v-spacer></v-spacer>

      <v-btn icon>
        <v-icon>mdi-dots-vertical</v-icon>
      </v-btn>

      <template v-slot:extension>
        <v-tabs fixed-tabs v-model="tab">
          <v-tabs-slider></v-tabs-slider>
          <v-tab>
            <v-icon>mdi-view-list</v-icon>
            Projects
          </v-tab>

          <v-tab>
            <v-icon>mdi-plus-box</v-icon>
            <span> Create </span>
          </v-tab>
        </v-tabs>
      </template>
    </v-toolbar>
    <v-tabs-items v-model="tab">
      <v-tab-item>
        {{ projects }}
      </v-tab-item>
      <v-tab-item>
        <ProjectCreation />
      </v-tab-item>
    </v-tabs-items>
  </v-card>
</template>
<script lang="ts">
import Vue from "vue";
import ProjectCreation from "@/components/ProjectCreate.vue";
import { components } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";

const ListProjectURL = "/api/project/";
type Project = components["schemas"]["Project"];

interface Data {
  tab: number;
  projects: Project[];
}

export default Vue.extend({
  data(): Data {
    return {
      tab: 0,
      projects: [],
    };
  },
  components: {
    ProjectCreation,
  },
  async mounted() {
    await this.getProjects();
  },
  methods: {
    async getProjects() {
      const result = await getWithRefreshToken<Project[]>(
        AuthModule,
        ListProjectURL
      );
      if (result === null) return;
      this.projects = result;
    },
  },
});
</script>
