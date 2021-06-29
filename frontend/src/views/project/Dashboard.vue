<style>
.dashboard-item {
  margin: 10px;
  padding-top: 0px;
  padding-bottom: 10px;
  text-align: center;
  height: 100px;
  display: flex;
  align-items: center;
}
.dashboard-item > div {
  width: 100%;
  vertical-align: middle;
}
</style>
<template>
  <div>
    <template v-if="projectDetail !== null && projectSummary !== null">
      <div class="pa-4 d-flex align-end">
        <div class="text-h4 text-left pa">
          Project "{{ projectDetail.name }}"
        </div>
        <div class="text-subtitle-2 pl-4">
          created on {{ projectDetail.ins_datetime }}
        </div>
        <div class="flex-grow-1"></div>
        <div v-if="false">
          <v-btn color="red" dark><v-icon>mdi-delete</v-icon> delete</v-btn>
        </div>
      </div>
      <div></div>
      <v-row v-if="projectSummary !== null">
        <v-col cols="4">
          <v-card class="dashboard-item" link :to="{ name: 'data-list' }">
            <div class="text-h6">
              <v-icon>mdi-folder</v-icon>
              {{ projectSummary.n_data }} Data
            </div>
          </v-card>
        </v-col>
        <v-col cols="4">
          <v-card class="dashboard-item" link :to="{ name: 'tuning-job-list' }">
            <div class="text-h6">
              <v-icon>mdi-tune</v-icon>
              {{ projectSummary.n_complete_jobs }} Tuning Results
            </div>
          </v-card>
        </v-col>
        <v-col cols="4">
          <v-card
            class="text-center dashboard-item"
            link
            :to="{ name: 'trained-model-list' }"
          >
            <div class="text-h6">
              <v-icon>mdi-calculator</v-icon>
              {{ projectSummary.n_models }} Trained Models
            </div>
          </v-card>
        </v-col>
      </v-row>
      <div class="mt-8">
        <v-btn color="primary" block>
          <v-icon>mdi-tune</v-icon> Start upload -> tuning</v-btn
        >
      </div>
    </template>
  </div>
</template>
<script lang="ts">
import Vue from "vue";
import { paths, components } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";

const ProjectSummaryURL = "/api/project_summary/";
type ProjectSummary =
  paths["/api/project_summary/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];

interface Data {
  projectSummary: ProjectSummary | null;
}

export default Vue.extend({
  data(): Data {
    return {
      projectSummary: null,
    };
  },
  methods: {
    async fetchProjectSummary(): Promise<void> {
      this.projectSummary = await getWithRefreshToken<ProjectSummary>(
        AuthModule,
        `${ProjectSummaryURL}${this.projectId}/`
      );
    },
  },
  async mounted() {
    await this.fetchProjectSummary();
  },
  computed: {
    projectId(): number | null {
      return AuthModule.currentProjectId;
    },
    projectDetail() {
      return AuthModule.currentProjectDetail;
    },
  },
});
</script>
