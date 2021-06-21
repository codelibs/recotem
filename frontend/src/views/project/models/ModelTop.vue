<style>
table.param-table th {
  min-width: 300px;
}
</style>
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
    <div class="mt-8">
      <v-expansion-panels v-model="panel" multiple>
        <v-expansion-panel>
          <v-expansion-panel-header>
            <div>
              <div>
                Model Configuration
                <span v-if="configuration !== null" class="text-caption ml-2">
                  (id: {{ configuration.id }})</span
                >
              </div>
            </div>
          </v-expansion-panel-header>
          <v-expansion-panel-content>
            <div v-if="configuration !== null">
              <div class="text-h5 pb-1">
                {{ configuration.recommender_class_name }}
              </div>
              <v-divider></v-divider>
              <div class="pt-1" v-if="modelParameters !== null">
                <table class="param-table">
                  <tbody>
                    <tr v-for="(param, i) in modelParameters" :key="i">
                      <th>{{ param.key }}</th>
                      <td></td>
                      {{
                        param.val
                      }}
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </v-expansion-panel-content>
        </v-expansion-panel>

        <v-expansion-panel>
          <v-expansion-panel-header>Preview results</v-expansion-panel-header>
          <v-expansion-panel-content>
            <div class="text-center pb-4 pt-2">
              <v-btn @click="fetchRawSample" :disabled="downloading">
                Get Sample Rec
              </v-btn>
            </div>
            <div v-if="rawRecommendationSample !== null">
              {{ rawRecommendationSample }}
            </div>
          </v-expansion-panel-content>
        </v-expansion-panel>
      </v-expansion-panels>
    </div>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { baseURL } from "@/env";
import { paths, components } from "@/api/schema";
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

const modelConfigURL = "/api/model_configuration/";
type ModelConfigResponse =
  paths["/api/model_configuration/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];

type RawRecommendationSample =
  paths["/api/trained_model/{id}/sample_recommendation_raw/"]["get"]["responses"]["200"]["content"]["application/json"];

type Data = {
  modelBasicInfo: respose200 | null;
  configuration: ModelConfigResponse | null;
  downloading: boolean;
  rawRecommendationSample: RawRecommendationSample | null;
  previewRequesting: boolean;
  panel: number[];
};
export default Vue.extend({
  data(): Data {
    return {
      modelBasicInfo: null,
      configuration: null,
      downloading: false,
      rawRecommendationSample: null,
      previewRequesting: false,
      panel: [0],
    };
  },
  async mounted() {
    await this.fetchInfo();
  },
  watch: {
    async projectId() {
      await this.fetchInfo();
    },
  },
  methods: {
    async fetchRawSample(): Promise<void> {
      if (this.trainedModelId === null) return;
      this.previewRequesting = true;
      const url = `/api/trained_model/${this.trainedModelId}/sample_recommendation_raw/`;
      let result = await getWithRefreshToken<RawRecommendationSample>(
        AuthModule,
        url
      );
      this.rawRecommendationSample = result;
      this.previewRequesting = false;
    },
    async fetchInfo(): Promise<void> {
      if (this.trainedModelId === null) return;
      let result = await getWithRefreshToken<respose200>(
        AuthModule,
        `${retrieveURL}/${this.trainedModelId}`
      );
      if (result === null) return;
      this.modelBasicInfo = result;
      let config = await getWithRefreshToken<ModelConfigResponse>(
        AuthModule,
        `${modelConfigURL}/${result.configuration}`
      );
      if (result === null) return;
      this.configuration = config;
    },
    async handleDownload(): Promise<void> {
      if (
        this.trainedModelId === null ||
        this.modelBasicInfo === null ||
        this.modelBasicInfo.basename === null
      )
        return;
      this.downloading = true;
      const URL = `${baseURL}/api/trained_model/${this.trainedModelId}/download_file/`;
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
    modelParameters(): { key: string; val: number | string | null }[] | null {
      if (this.configuration === null) return null;
      const configRecord: Record<string, number | string | null> = JSON.parse(
        this.configuration.parameters_json
      );
      let m = Object.keys(configRecord);
      return m.map((key) => {
        return { key, val: configRecord[key] };
      });
    },
  },
});
</script>
