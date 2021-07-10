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
  methods: {
    async fetchProjectDetail() {
      const projectId = AuthModule.currentProjectId;
      const project = await getWithRefreshToken<Project>(
        AuthModule,
        ProjectListUrl + `/${projectId}`
      );
      AuthModule.setProjectDetail(project);
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
