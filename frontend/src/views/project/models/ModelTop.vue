<template>
  <div v-if="modelBasicInfo !== null">
    <div class="d-flex align-end">
      <div class="text-h5">Model {{ modelBasicInfo.id }}</div>
      <div class="text-subtitle-2 pl-8" v-if="modelBasicInfo.basename !== null">
        Saved as {{ modelBasicInfo.file }}
        <span v-if="modelBasicInfo.filesize !== null">
          , {{ prettyFileSize }}
        </span>
      </div>
      <div class="flex-grow-1"></div>
      <div>
        <v-btn
          :color="downloading ? '' : 'info'"
          @click="handleDownload"
          :disabled="downloading"
        >
          <template v-if="!downloading">
            <v-icon> mdi-download </v-icon>
            <span class="pa-1"></span>
            Download
          </template>
          <template v-else>
            <v-progress-circular indeterminate :size="10" />
            <span class="text-subtitle pl-4"> Downloading...</span>
          </template>
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
import { baseURL } from "@/env";
import { paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import {
  getWithRefreshToken,
  downloadWithRefreshToken,
} from "@/utils/request.ts";
import { prettyFileSize } from "@/utils/conversion";

//import TuningJobList from "@/components/TuningJobList.vue";

const retrieveURL = "/api/trained_model/";
type responses = paths["/api/trained_model/{id}/"]["get"]["responses"];
type respose200 = responses["200"]["content"]["application/json"];
type download = paths["/api/training_data/{id}/download_file/"];

type Data = {
  modelBasicInfo: respose200 | null;
  downloading: boolean;
};
export default Vue.extend({
  data(): Data {
    return {
      modelBasicInfo: null,
      downloading: false,
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
    async handleDownload(): Promise<void> {
      if (
        this.trainedModelId === null ||
        this.modelBasicInfo === null ||
        this.modelBasicInfo.basename === null
      )
        return;
      this.downloading = true;
      const URL = `${baseURL}/api/training_data/${this.trainedModelId}/download_file/`;
      await downloadWithRefreshToken(
        AuthModule,
        URL,
        this.modelBasicInfo.basename
      );
      this.downloading = false;
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
