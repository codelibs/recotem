<template>
  <div v-if="dataBasicInfo !== null">
    <v-row class="pb-2">
      <v-col cols="8">
        <div class="text-h5">Tuning Job {{ dataBasicInfo.id }}</div>
        <div class="text-subtitle-1">
          Stasus: {{ dataBasicInfo.taskandparameterjoblink_set }}
        </div>
      </v-col>
    </v-row>
    <router-view> </router-view>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";

const retrieveURL = "/api/parameter_tuning_job";
type JobDetailType =
  paths["/api/parameter_tuning_job/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];

type Data = {
  dataBasicInfo: JobDetailType | null;
};
export default Vue.extend({
  data(): Data {
    return {
      dataBasicInfo: null,
    };
  },
  async mounted() {
    await this.fetchTrainingData();
  },
  watch: {
    async projectId() {
      await this.fetchTrainingData();
    },
  },
  methods: {
    async fetchTrainingData(): Promise<void> {
      if (this.parameterTuningJobId === null) return;
      let result = await getWithRefreshToken<JobDetailType>(
        AuthModule,
        `${retrieveURL}/${this.parameterTuningJobId}`
      );
      if (result === null) return;
      console.log(result);
      this.dataBasicInfo = result;
    },
  },
  computed: {
    parameterTuningJobId(): number | null {
      try {
        return parseInt(this.$route.params.parameterTuningJobId);
      } catch {
        return null;
      }
    },
  },
});
</script>
