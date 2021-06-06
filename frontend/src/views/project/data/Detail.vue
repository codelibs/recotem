<template>
  <v-card v-if="dataDetail !== undefined">
    <v-container fluid>
      <v-row>
        <v-col class="flex-grow1">
          <div class="text-h5">Tuning Jobs</div>
        </v-col>
        <v-spacer></v-spacer>
        <v-col>
          <v-btn color="info" dark :to="{ name: 'start-tuning-with-data' }">
            <v-icon> mdi-tune </v-icon>
            <span class="pl-2"> Start New Job </span>
          </v-btn>
        </v-col>
      </v-row>

      <tuning-job-list
        :parameterTuningJobList="dataDetail.parametertuningjob_set"
        v-if="dataDetail.parametertuningjob_set.length > 0"
      />
      <div v-else class="text-h6 text-center pt-6 pb-6">No jobs.</div>
    </v-container>
  </v-card>
</template>
<script lang="ts">
import Vue from "vue";
import { components, paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";
import TuningJobList from "@/components/TuningJobList.vue";
type TrainingDataType = components["schemas"]["TrainingDataDetail"];

const retrieveURL = "/api/data_detail";
const dataDetailURL = "/api/data_detail/{id}";
type responses = paths["/api/data_detail/{id}/"]["get"]["responses"];
type respose200 = responses["200"]["content"]["application/json"];

type Data = {
  dataDetail: respose200 | undefined;
};
export default Vue.extend({
  data(): Data {
    return {
      dataDetail: undefined,
    };
  },
  async mounted() {
    await this.fetchTrainingDataDetail();
  },
  watch: {
    async projectId() {
      await this.fetchTrainingDataDetail();
    },
  },
  methods: {
    async fetchTrainingDataDetail(): Promise<void> {
      if (this.dataId === null) return;
      let result = await getWithRefreshToken<TrainingDataType>(
        AuthModule,
        `${retrieveURL}/${this.dataId}`
      );
      if (result === null) return;
      console.log(result);
      this.dataDetail = result;
    },
  },
  components: {
    TuningJobList,
  },
  computed: {
    dataId(): number | null {
      try {
        return parseInt(this.$route.params.dataId);
      } catch {
        return null;
      }
    },
  },
});
</script>
