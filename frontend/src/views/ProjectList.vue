<template>
  <div>
    <v-toolbar flat>
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
    </v-toolbar>

    <v-container>
      <v-tabs-items v-model="tab">
        <v-tab-item>
          <v-list v-if="projects.length > 0">
            <template v-for="(project, i) in projects">
              <v-list-item
                :key="i"
                :to="{ name: 'project', params: { projectId: project.id } }"
              >
                <v-list-item-content>
                  <v-list-item-title>
                    {{ project.name }}
                  </v-list-item-title>
                  <v-list-item-subtitle>
                    Created on {{ project.ins_datetime }}
                  </v-list-item-subtitle>
                </v-list-item-content>
              </v-list-item>
              <v-divider :key="i + 0.5" v-if="i < projects.length - 1">
              </v-divider>
            </template>
          </v-list>
          <div v-else class="pa-8 text-h6 text-center">No projects yet.</div>
        </v-tab-item>
        <v-tab-item>
          <ProjectCreation />
        </v-tab-item>
      </v-tabs-items>
    </v-container>
  </div>
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
