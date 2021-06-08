<template>
  <div v-if="modelBasicInfo !== null">
    <div class="d-flex align-end">
      <div class="text-h5">Model {{ modelBasicInfo.id }}</div>
      <div class="text-subtitle-2 pl-8" v-if="modelBasicInfo.basename !== null">
        Saved as {{ modelBasicInfo.model_path }}
        <span v-if="modelBasicInfo.filesize !== null">
          , {{ prettyFileSize }}
        </span>
      </div>
      <div class="flex-grow-1"></div>
      <div>
        <v-btn color="info">
          <v-icon> mdi-download </v-icon>
          <span class="pa-1"></span>
          Download
        </v-btn>
      </div>
    </div>
    <div>
      <div class="text-center text-h6 pa-8">[Todo] model preview...</div>
    </div>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";
import { prettyFileSize } from "@/utils/conversion";

//import TuningJobList from "@/components/TuningJobList.vue";

const retrieveURL = "/api/trained_model/";
type responses = paths["/api/trained_model/{id}/"]["get"]["responses"];
type respose200 = responses["200"]["content"]["application/json"];

type Data = {
  modelBasicInfo: respose200 | null;
};
export default Vue.extend({
  data(): Data {
    return {
      modelBasicInfo: null,
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
      if (this.trainedModelId === null) return;
      let result = await getWithRefreshToken<respose200>(
        AuthModule,
        `${retrieveURL}/${this.trainedModelId}`
      );
      if (result === null) return;
      console.log(result);
      this.modelBasicInfo = result;
    },
  },
  computed: {
    trainedModelId(): number | null {
      try {
        return parseInt(this.$route.params.trainedModelId);
      } catch {
        return null;
      }
    },
    prettyFileSize(): string {
      return prettyFileSize(this.modelBasicInfo?.filesize || null);
    },
  },
});
</script>
