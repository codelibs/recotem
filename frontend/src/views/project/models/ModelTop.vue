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
          v-if="modelBasicInfo.filesize !== null"
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

        <v-expansion-panel v-if="modelBasicInfo.filesize !== null">
          <v-expansion-panel-header>Preview results</v-expansion-panel-header>
          <v-expansion-panel-content>
            <div class="d-flex align-center pa-0">
              <v-btn @click="fetchSample" :disabled="downloading">
                <v-icon>mdi-refresh</v-icon> Sample
              </v-btn>
              <div class="flex-grow-1"></div>
              <div>
                <v-select
                  label="Item meta-data to view"
                  v-if="itemMetaDataList"
                  :items="itemMetaDataList"
                  item-value="id"
                  v-model="itemMetaDataId"
                >
                  <template v-slot:selection="{ item }">
                    <div>
                      <span class="mr-2"> {{ item.basename }} </span>
                      <span class="text-subtitle-2">(id: {{ item.id }}) </span>
                    </div>
                  </template>
                  <!-- 選択一覧 -->
                  <template v-slot:item="{ item }">
                    <span class="mr-2"> {{ item.basename }} </span>
                    <span class="text-subtitle-2">(id: {{ item.id }}) </span>
                  </template>
                </v-select>
              </div>
            </div>
            <div
              v-if="rawRecommendationSample !== null && !previewWithMetaData"
            >
              {{ rawRecommendationSample }}
            </div>
            <div
              v-if="
                previewWithMetaData && metadataRecommendationSample !== null
              "
            >
              <div>
                <v-autocomplete
                  label="Columns to show"
                  multiple
                  chips
                  :items="itemMetaDataColumns"
                  v-model="shownColumns"
                ></v-autocomplete>
              </div>
              <v-data-table
                dense
                :items="metadataRecommendationSample.profile"
                :headers="profileMetaHeader"
              >
                <template v-slot:top>
                  <div class="text-h6">
                    Interaction log for user
                    {{ metadataRecommendationSample.userId }}:
                  </div>
                </template>
              </v-data-table>
              <v-data-table
                dense
                :items="metadataRecommendationSample.recommendation"
                :headers="recommendationMetaHeader"
              >
                <template v-slot:top>
                  <div class="text-h6">
                    Recommended items for
                    {{ metadataRecommendationSample.userId }}:
                  </div>
                </template>
              </v-data-table>
            </div>
          </v-expansion-panel-content>
        </v-expansion-panel>
      </v-expansion-panels>
    </div>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import qs from "qs";
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

const itemMetaDataListURL = "/api/item_meta_data/";
type ItemMetaDataListResponse =
  paths["/api/item_meta_data/"]["get"]["responses"]["200"]["content"]["application/json"];
type ItemMetaDataList = ItemMetaDataListResponse["results"];
type ItemMetaData = components["schemas"]["ItemMetaData"];

const recommendationURL =
  "/api/trained_model/{id}/sample_recommendation_metadata/{metadata_id}/";
type RecommendationWithItemMetaDataSample =
  paths["/api/trained_model/{id}/sample_recommendation_metadata/{metadata_id}/"]["get"]["responses"]["200"]["content"]["application/json"];

type ProjectInfo =
  paths["/api/project/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];

type MetadataRecommendationSample = {
  userId: any;
  profile: any[];
  recommendation: any[];
};
type Data = {
  modelBasicInfo: respose200 | null;
  configuration: ModelConfigResponse | null;
  downloading: boolean;
  previewWithMetaData: boolean;
  rawRecommendationSample: RawRecommendationSample | null;
  metadataRecommendationSample: MetadataRecommendationSample | null;
  previewRequesting: boolean;
  panel: number[];
  itemMetaDataList: ItemMetaDataList;
  itemMetaDataId: number | null;
  shownColumns: string[];
  projectInfo: ProjectInfo | null;
};
export default Vue.extend({
  data(): Data {
    return {
      modelBasicInfo: null,
      configuration: null,
      downloading: false,
      rawRecommendationSample: null,
      previewWithMetaData: false,
      metadataRecommendationSample: null,
      previewRequesting: false,
      panel: [],
      itemMetaDataList: undefined,
      itemMetaDataId: null,
      shownColumns: [],
      projectInfo: null,
    };
  },
  async mounted() {
    await this.fetchInfo();
  },
  watch: {
    async projectId() {
      await this.fetchInfo();
    },
    itemMetaDataColumns(nv: string[] | null) {
      if (nv === null) return;
      this.shownColumns = nv.slice(0, Math.min(nv.length, 3));
    },
  },
  methods: {
    async fetchSample(): Promise<void> {
      if (this.trainedModelId === null) return;

      this.previewRequesting = true;
      if (this.itemMetaDataId === null) {
        const url = `/api/trained_model/${this.trainedModelId}/sample_recommendation_raw/`;
        let result = await getWithRefreshToken<RawRecommendationSample>(
          AuthModule,
          url
        );
        this.rawRecommendationSample = result;
        this.previewRequesting = false;
      } else {
        const url = `/api/trained_model/${this.trainedModelId}/sample_recommendation_metadata/${this.itemMetaDataId}/`;
        let result =
          await getWithRefreshToken<RecommendationWithItemMetaDataSample>(
            AuthModule,
            url
          );
        if (result === null) return;
        this.metadataRecommendationSample = {
          profile: JSON.parse(result.user_profile),
          userId: result.user_id,
          recommendation: JSON.parse(result.recommendations),
        };
        this.previewWithMetaData = true;
      }
    },
    async fetchInfo(): Promise<void> {
      if (this.trainedModelId === null) return;

      let result = await getWithRefreshToken<respose200>(
        AuthModule,
        `${retrieveURL}/${this.trainedModelId}`
      );
      if (result === null) {
        alert(`Failed to fetch ${retrieveURL}/${this.trainedModelId}`);
        return;
      }
      this.modelBasicInfo = result;
      let config = await getWithRefreshToken<ModelConfigResponse>(
        AuthModule,
        `${modelConfigURL}/${result.configuration}`
      );
      if (config === null) {
        alert(`Failed to fetch ${modelConfigURL}/${result.configuration}`);
        return;
      }

      this.configuration = config;

      let metaDataList: ItemMetaData[] = [];
      for (let page = 1; ; page++) {
        let fetchURL =
          itemMetaDataListURL +
          `?${qs.stringify({ page_size: 10, project: config.project, page })}`;
        let itemMetaDataListResponse =
          await getWithRefreshToken<ItemMetaDataListResponse>(
            AuthModule,
            fetchURL
          );
        console.log(itemMetaDataListResponse);
        if (itemMetaDataListResponse === null) {
          break;
        }

        if (itemMetaDataListResponse.results === undefined) {
          continue;
        } else {
          metaDataList = metaDataList.concat(itemMetaDataListResponse.results);
        }

        if (!itemMetaDataListResponse.next) {
          break;
        }
      }

      this.itemMetaDataList = metaDataList;

      let project = await getWithRefreshToken<ProjectInfo>(
        AuthModule,
        `/api/project/${config.project}/`
      );
      this.projectInfo = project;
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
    selectedItemMetaData(): ItemMetaData | undefined {
      if (this.itemMetaDataId === null) return undefined;
      if (this.itemMetaDataList === undefined) return undefined;
      return this.itemMetaDataList.find((v) => v.id === this.itemMetaDataId);
    },
    profileMetaHeader():
      | { text: string; value: string; sortable: boolean }[]
      | null {
      if (this.shownColumns === null) return null;
      if (this.projectInfo === null) return null;
      let c = [
        {
          text: this.projectInfo.item_column,
          value: this.projectInfo.item_column,
          sortable: true,
        },
      ];
      return c.concat(
        this.shownColumns.map((v) => {
          return { text: v, value: v, sortable: true };
        })
      );
    },
    recommendationMetaHeader():
      | { text: string; value: string; sortable: boolean }[]
      | null {
      if (this.shownColumns === null) return null;
      if (this.projectInfo === null) return null;

      let c = [
        {
          text: this.projectInfo.item_column,
          value: this.projectInfo.item_column,
          sortable: false,
        },
        {
          text: "score",
          value: "score",
          sortable: true,
        },
      ];
      return c.concat(
        this.shownColumns.map((v) => {
          return { text: v, value: v, sortable: false };
        })
      );
    },
    itemMetaDataColumns(): string[] | undefined {
      if (this.selectedItemMetaData === undefined) return undefined;
      return JSON.parse(this.selectedItemMetaData.valid_columns_list_json);
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
