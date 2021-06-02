<template>
  <v-row>
    <v-col cols="2" v-if="project !== null">
      <v-list nav>
        <v-list-item>
          <v-list-item-content>
            <v-list-item-title>
              {{ project.name }}
            </v-list-item-title>
          </v-list-item-content>
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
      </v-list>
    </v-col>
    <v-divider vertical></v-divider>
    <v-col>
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
      }
      this.project = project;
    },
  },
  async mounted() {
    await this.fetchProjectDetail(parseInt(this.$route.params.projectId));
  },
});
</script>
