<template>
  <div class="mt-1">
    <v-card>
      <training-data-list />
    </v-card>
    <div class="ma-8"></div>
    <v-card>
      <v-data-table
        :items="itemMetaData"
        :server-items-length="itemMetaTotalSize"
        :headers="itemMetaDataHeaders"
        :options.sync="options"
      >
        <template v-slot:top>
          <v-toolbar flat>
            <v-toolbar-title>Item Meta Data</v-toolbar-title>
            <v-spacer></v-spacer>

            <data-upload
              v-model="itemMetaDataUploadDialogue"
              :projectId="projectId"
              postURL="/api/item_meta_data/"
              fileLabel="An item meta-data file."
            ></data-upload>
          </v-toolbar>
        </template>
        <template v-slot:[`item.filesize`]="{ value }">
          {{ prettyFileSize(value) }}
        </template>
        <template v-slot:[`item.ins_datetime`]="{ value }">
          <span class="text-caption">{{ prettifyDate(value) }} </span>
        </template>
      </v-data-table>
    </v-card>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import qs from "qs";
import { paths } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { prettyFileSize } from "@/utils/conversion";
import { prettifyDate } from "@/utils/date";
import { DataTableOptions, DataTableHeader } from "@/utils/table";

import { AuthModule } from "@/store/auth";
import { AxiosError } from "axios";

import DataUpload from "@/components/DataUpload.vue";
import TrainingDataList from "@/components/TrainingDataList.vue";

const itemMetaDataListURL = "/api/item_meta_data/";
type itemMetaResponseType =
  paths["/api/item_meta_data/"]["get"]["responses"]["200"]["content"]["application/json"];
type itemMetaResponseContent = itemMetaResponseType["results"];

const pageSize = 5;

type Data = {
  itemMetaData: itemMetaResponseContent;
  itemMetaTotalSize: number | null;
  itemMetaDataUploadDialogue: boolean;
  itemMetaDataHeaders: DataTableHeader[];
  options: DataTableOptions;
  loading: false;
};

export default Vue.extend({
  data(): Data {
    return {
      itemMetaData: undefined,
      itemMetaTotalSize: null,
      itemMetaDataUploadDialogue: false,
      itemMetaDataHeaders: [
        { text: "id", value: "id", sortable: false },
        { text: "filename", value: "basename", sortable: false },
        { text: "file size", value: "filesize", sortable: false },
        { text: "upload date", value: "ins_datetime", sortable: false },
      ],
      options: { page: 1, itemsPerPage: 5 },
      loading: false,
    };
  },
  computed: {
    projectId(): number | null {
      return AuthModule.currentProjectId;
    },
  },
  watch: {
    options: {
      deep: true,
      async handler() {
        await this.fetchItemMetaData();
      },
    },
    async itemMetaDataUploadDialogue() {
      await this.fetchItemMetaData();
    },
  },
  methods: {
    prettyFileSize(value: number | null) {
      return prettyFileSize(value);
    },
    prettifyDate(value: string): string {
      return prettifyDate(value);
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
            page: this.options.page,
            page_size: this.options.itemsPerPage,
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
    await this.fetchItemMetaData();
  },
  components: {
    DataUpload,
    TrainingDataList,
  },
});
</script>
