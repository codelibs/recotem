<template>
  <v-row class="fill-height">
    <v-col cols="2" v-if="project !== null">
      <v-list nav>
        <v-list-item>
          <v-list-item-icon>
            <v-btn icon dark color="primary" :to="{ name: 'project' }">
              <v-icon> mdi-home</v-icon>
            </v-btn>
          </v-list-item-icon>
          <v-list-item-title>
            <span class="text-caption"> Project </span><br />
            {{ project.name }}
          </v-list-item-title>
        </v-list-item>
        <v-divider></v-divider>
        <v-list-item
          :to="{ name: 'data-list', params: { projectId } }"
          link
          :ripple="false"
        >
          <v-list-item-icon>
            <v-icon> mdi-folder</v-icon>
          </v-list-item-icon>
          <v-list-item-title> Data </v-list-item-title>
        </v-list-item>
        <v-list-item
          :to="{ name: 'tuning-job-list', params: { projectId } }"
          link
          :ripple="false"
        >
          <v-list-item-icon>
            <v-icon> mdi-tune</v-icon>
          </v-list-item-icon>
          <v-list-item-title> Tuning </v-list-item-title>
        </v-list-item>
        <v-list-item link :ripple="false" :to="{ name: 'trained-model-list' }">
          <v-list-item-icon>
            <v-icon> mdi-calculator</v-icon>
          </v-list-item-icon>
          <v-list-item-title> Models </v-list-item-title>
        </v-list-item>
      </v-list>
    </v-col>
    <v-divider vertical></v-divider>
    <v-col cols="10">
      <v-container>
        <router-view></router-view>
      </v-container>
    </v-col>
  </v-row>
</template>
<script lang="ts">
import Vue from "vue";
import { components } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";
import { AxiosError } from "axios";
const ProjectListUrl = "/api/project";

type Project = components["schemas"]["Project"];
type Data = {
  project: Project | null;
};

export default Vue.extend({
  data(): Data {
    return {
      project: null,
    };
  },
  computed: {
    projectId(): number | undefined {
      return this.project?.id;
    },
  },
  methods: {
    async fetchProjectDetail(projectId: number) {
      const project = await getWithRefreshToken<Project>(
        AuthModule,
        ProjectListUrl + `/${projectId}`
      ).catch((error: AxiosError) => {
        if (error.response?.status == 404) {
          alert(`Project ${projectId} not found.`);
        }
        return null;
      });
      if (project === null) {
        this.$router.push({ name: "project-list" });
      } else {
        AuthModule.setProjectDetail(project);
      }
      this.project = project;
    },
    async loadProject() {
      const projectId = parseInt(this.$route.params.projectId);
      AuthModule.setProjectId(projectId);
      await this.fetchProjectDetail(projectId);
    },
  },
  async mounted() {
    await this.loadProject();
  },
});
</script>

<style scoped>
.active {
  background-color: aqua;
}
</style>
