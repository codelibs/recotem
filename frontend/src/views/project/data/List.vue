<template>
  <div class="mt-1">
    <div class="d-flex align-center">
      <div class="text-h6 pl-4">Training Data</div>

      <div class="flex-grow-1"></div>
      <data-upload
        v-model="trainingDataUploadDialogue"
        :projectId="projectId"
        postURL="/api/training_data/"
        fileLabel="A training data file."
      ></data-upload>
    </div>

    <v-container v-if="trainingData !== undefined && trainingData.length > 0">
      <v-list>
        <v-divider></v-divider>
        <template v-for="(td, i) in trainingData">
          <v-list-item
            :key="i"
            :to="{ name: 'data-detail', params: { dataId: td.id } }"
          >
            <v-list-item-content>
              <v-list-item-title>
                {{ td.basename }}
              </v-list-item-title>
              <v-list-item-subtitle>
                <v-row>
                  <v-col cols="5"> {{ td.ins_datetime }} </v-col>
                  <v-col cols="4" class="ml-4">
                    {{ prettyFileSize(td.filesize) }}
                  </v-col>
                  <v-spacer></v-spacer>
                </v-row>
              </v-list-item-subtitle>
            </v-list-item-content>
            <v-list-item-action>
              <v-btn
                icon
                ripple=""
                :to="{
                  name: 'start-tuning-with-data',
                  params: { dataId: td.id },
                }"
              >
                <v-icon color="primary">mdi-tune</v-icon>
              </v-btn>
            </v-list-item-action>
          </v-list-item>
          <v-divider :key="i + 0.5"></v-divider>
        </template>
      </v-list>
      <v-pagination
        v-if="maxPageSize !== null && maxPageSize > 1"
        v-model="pageNumber"
        :length="maxPageSize"
      >
      </v-pagination>
    </v-container>
    <div v-else class="text-center pt-12 text-h6">No training data yet.</div>

    <div class="d-flex align-center pt-8">
      <div class="text-h6 pl-4">Item Meta-Data</div>

      <div class="flex-grow-1"></div>
      <data-upload
        v-model="itemMetaDataUploadDialogue"
        :projectId="projectId"
        postURL="/api/item_meta_data/"
        fileLabel="An item meta-data file."
      ></data-upload>
    </div>

    <v-container v-if="itemMetaData !== undefined && itemMetaData.length > 0">
      <v-list>
        <v-divider></v-divider>
        <template v-for="(td, i) in itemMetaData">
          <v-list-item :key="i">
            <v-list-item-content>
              <v-list-item-title>
                {{ td.basename }}
              </v-list-item-title>
              <v-list-item-subtitle>
                <v-row>
                  <v-col cols="5"> {{ td.ins_datetime }} </v-col>
                  <v-col cols="4" class="ml-4">
                    {{ prettyFileSize(td.filesize) }}
                  </v-col>
                  <v-spacer></v-spacer>
                </v-row>
              </v-list-item-subtitle>
            </v-list-item-content>
          </v-list-item>
          <v-divider :key="i + 0.5"></v-divider>
        </template>
      </v-list>
      <v-pagination
        v-if="itemMetaMaxPageSize !== null && itemMetaMaxPageSize > 1"
        v-model="itemMetaPageNumber"
        :length="itemMetaMaxPageSize"
      >
      </v-pagination>
    </v-container>
    <div v-else class="text-center pt-12 text-h6">No item meta-data yet.</div>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import qs from "qs";
import { components, paths } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { prettyFileSize } from "@/utils/conversion";
import { computeMaxPage } from "@/utils/pagination";

import { AuthModule } from "@/store/auth";
import { AxiosError } from "axios";

import DataUpload from "@/components/DataUpload.vue";

const trainingDataListURL = "/api/training_data/";
const itemMetaDataListURL = "/api/item_meta_data/";
type TrainingData = components["schemas"]["TrainingData"];
type responseType =
  paths["/api/training_data/"]["get"]["responses"]["200"]["content"]["application/json"];
type responseContent = responseType["results"];
type itemMetaResponseType =
  paths["/api/item_meta_data/"]["get"]["responses"]["200"]["content"]["application/json"];
type itemMetaResponseContent = itemMetaResponseType["results"];

const pageSize = 5;

type Data = {
  trainingData: responseContent;
  itemMetaData: itemMetaResponseContent;
  pageNumber: number;
  itemMetaPageNumber: number;
  totalSize: number | null;
  itemMetaTotalSize: number | null;
  maxPageNumber: number | null;
  itemMetaMaxPageNumber: number | null;
  trainingDataUploadDialogue: boolean;
  itemMetaDataUploadDialogue: boolean;
};

export default Vue.extend({
  data(): Data {
    return {
      trainingData: undefined,
      itemMetaData: undefined,
      pageNumber: 1,
      itemMetaPageNumber: 1,
      totalSize: null,
      itemMetaTotalSize: null,
      maxPageNumber: null,
      itemMetaMaxPageNumber: null,
      trainingDataUploadDialogue: false,
      itemMetaDataUploadDialogue: false,
    };
  },
  computed: {
    maxPageSize(): number | null {
      return computeMaxPage(this.totalSize, pageSize);
    },
    itemMetaMaxPageSize(): number | null {
      return computeMaxPage(this.itemMetaTotalSize, pageSize);
    },
    projectId(): number | null {
      try {
        return parseInt(this.$route.params.projectId);
      } catch {
        return null;
      }
    },
  },
  watch: {
    async pageNumber() {
      await this.fetchData();
    },
    async itemMetaPageNumber() {
      await this.fetchItemMetaData();
    },
    async trainingDataUploadDialogue() {
      await this.fetchData();
    },
    async itemMetaDataUploadDialogue() {
      await this.fetchItemMetaData();
    },
  },
  methods: {
    prettyFileSize(value: number | null) {
      return prettyFileSize(value);
    },
    async fetchData() {
      if (this.projectId === null) {
        return;
      }
      let trainingData = await getWithRefreshToken<responseType>(
        AuthModule,
        trainingDataListURL +
          `?${qs.stringify({
            project: this.projectId,
            page: this.pageNumber,
            page_size: pageSize,
          })}`
      ).catch((error: AxiosError) => {
        console.log(error.response?.data);
        return null;
      });
      if (trainingData?.results !== undefined) {
        this.trainingData = trainingData.results;
        this.totalSize = trainingData.count || null;
      }
    },
    async fetchItemMetaData() {
      if (this.projectId === null) {
        return;
      }
      let itemMetaData = await getWithRefreshToken<itemMetaResponseType>(
        AuthModule,
        itemMetaDataListURL +
          `?${qs.stringify({
            project: this.projectId,
            page: this.itemMetaPageNumber,
            page_size: pageSize,
          })}`
      ).catch((error: AxiosError) => {
        console.log(error.response?.data);
        return null;
      });
      if (itemMetaData?.results !== undefined) {
        this.itemMetaData = itemMetaData.results;
        this.itemMetaTotalSize = itemMetaData.count || null;
      }
    },
  },
  async mounted() {
    await this.fetchData();
    await this.fetchItemMetaData();
  },
  components: {
    DataUpload,
  },
});
</script>
