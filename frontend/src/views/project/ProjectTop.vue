<template>
  <v-col cols="12">
    <v-container>
      <router-view></router-view>
    </v-container>
  </v-col>
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
    async fetchProjectDetail() {
      const projectId = AuthModule.currentProjectId;
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
      await this.fetchProjectDetail();
    },
  },
  async mounted() {
    await this.loadProject();
  },
  beforeRouteEnter(to, from, next) {
    const projectId = parseInt(to.params.projectId);
    AuthModule.setProjectId(projectId);
    next();
  },
});
</script>

<style scoped>
.active {
  background-color: aqua;
}
</style>
