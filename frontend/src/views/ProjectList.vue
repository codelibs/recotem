<template>
  <div>
    <v-toolbar color="#4c64f5" dark flat>
      <v-toolbar-title>Recotem Project Manager</v-toolbar-title>

      <v-spacer></v-spacer>

      <!--
      <v-btn icon>
        <v-icon>mdi-dots-vertical</v-icon>
      </v-btn>
      -->

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
        <v-list>
          <v-list-item
            v-for="(project, i) in projects"
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
            <v-list-item-action>
              <v-btn icon @click="deleteProject(project)">
                <v-icon>mdi-delete</v-icon>
              </v-btn>
            </v-list-item-action>
          </v-list-item>
        </v-list>
        <v-dialog :value="deleteTargetProject !== null"> </v-dialog>
      </v-tab-item>
      <v-tab-item>
        <ProjectCreation />
      </v-tab-item>
    </v-tabs-items>
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
  deleteTargetProject: Project | null;
}

export default Vue.extend({
  data(): Data {
    return {
      tab: 0,
      projects: [],
      deleteTargetProject: null,
    };
  },
  components: {
    ProjectCreation,
  },
  async mounted() {
    await this.getProjects();
  },
  methods: {
    async deleteProject(project: Project) {
      this.deleteTargetProject = project;
    },
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
