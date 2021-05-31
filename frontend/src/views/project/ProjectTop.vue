<template>
  <div class="fill-height">
    <v-row class="fill-height">
      <v-col cols="3">
        <v-navigation-drawer
          floating
          permanent
          dark
          color="#4c6ef5"
          v-if="project !== null"
        >
          <v-list>
            <v-list-item>
              <v-list-item-content>
                <v-list-item-title>
                  {{ project.name }}
                </v-list-item-title>
              </v-list-item-content>
            </v-list-item>
            <v-divider></v-divider>
            <v-list-item :to="{ name: 'data-list', params: { projectId } }">
              <v-list-item-content>
                <v-list-item-title> Data </v-list-item-title>
              </v-list-item-content>
            </v-list-item>
          </v-list>
        </v-navigation-drawer>
      </v-col>
      <v-col>
        <div>
          <!--<router-view></router-view>-->
          TEST
        </div>
      </v-col>
    </v-row>
  </div>
</template>
<script lang="ts">
import Vue from "vue";
import { components } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";
import { required } from "vee-validate/dist/rules";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import axios, { AxiosError } from "axios";
import { baseURL } from "@/env";
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
  async beforeRouteUpdate(to, from, next) {
    await this.fetchProjectDetail(parseInt(to.params.projectId));
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
