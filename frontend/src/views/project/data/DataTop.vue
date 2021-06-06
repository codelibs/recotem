<template>
  <div v-if="dataBasicInfo !== null">
    <v-row>
      <v-col cols="8">
        <div class="text-h5">
          Data {{ dataBasicInfo.basename }}
          <span class="text-subtitle-1"> (id: {{ dataBasicInfo.id }}) </span>
        </div>
        <div class="text-subtitle-1">
          Saved as {{ dataBasicInfo.upload_path }}
          <span v-if="dataBasicInfo.filesize !== null">
            , {{ prettyFileSize }}
          </span>
        </div>
      </v-col>
    </v-row>
    <router-view> </router-view>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { components, paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken, prettyFileSize } from "@/utils";

//import TuningJobList from "@/components/TuningJobList.vue";
type TrainingDataType = components["schemas"]["TrainingData"];

const retrieveURL = "/api/training_data/";
type responses = paths["/api/training_data/{id}/"]["get"]["responses"];
type respose200 = responses["200"]["content"]["application/json"];

type Data = {
  dataBasicInfo: respose200 | null;
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
      if (this.dataId === null) return;
      let result = await getWithRefreshToken<TrainingDataType>(
        AuthModule,
        `${retrieveURL}/${this.dataId}`
      );
      if (result === null) return;
      console.log(result);
      this.dataBasicInfo = result;
    },
  },
  computed: {
    dataId(): number | null {
      try {
        return parseInt(this.$route.params.dataId);
      } catch {
        return null;
      }
    },
    prettyFileSize(): string {
      return prettyFileSize(this.dataBasicInfo?.filesize || null);
    },
  },
});
</script>
