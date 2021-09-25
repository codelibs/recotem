<style lang="css" scoped>
.row-pointer >>> tbody tr :hover {
  cursor: pointer;
}
</style>

<template>
  <v-data-table
    :items="trainingData"
    disable-sort
    :headers="headers"
    :server-items-length="totalSize"
    :options.sync="options"
    :loading="loading"
    @click:row="clickRow"
    :class="{ 'row-pointer': !isSelection }"
    :dense="isSelection"
    :show-select="isSelection"
    :single-select="isSelection"
    v-model="selectedItem"
    data-table-name="training-data-list"
  >
    <template v-slot:top>
      <v-toolbar flat>
        <v-toolbar-title>Training Data</v-toolbar-title>
        <v-spacer></v-spacer>
        <DataUpload
          name="training-data-upload"
          v-if="!isSelection"
          v-model="trainingDataUploadDialogue"
          :projectId="projectId"
          postURL="/api/training_data/"
          fileLabel="A training data file."
          :caption="uploadCaption"
        ></DataUpload>
      </v-toolbar>
    </template>
    <template v-slot:[`item.filesize`]="{ value }">
      {{ prettyFileSize(value) }}
    </template>
    <template v-slot:[`item.ins_datetime`]="{ value }">
      <span class="text-caption">{{ prettifyDate(value) }} </span>
    </template>
    <template v-slot:[`item.actions`]="{ item }">
      <v-btn
        icon
        :to="{
          name: 'start-tuning-with-data',
          params: { dataId: item.id },
        }"
      >
        <v-icon color="primary">mdi-tune</v-icon>
      </v-btn>
    </template>
  </v-data-table>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import qs from "qs";
import { components, paths } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { prettyFileSize } from "@/utils/conversion";
import { prettifyDate } from "@/utils/date";
import { DataTableHeader, DataTableOptions } from "@/utils/table";

import { AuthModule } from "@/store/auth";

import DataUpload from "@/components/DataUpload.vue";

const trainingDataListURL = "/api/training_data/";
type responseType =
  paths["/api/training_data/"]["get"]["responses"]["200"]["content"]["application/json"];
type responseContent = responseType["results"];
type Row = components["schemas"]["TrainingData"];

type Data = {
  trainingData: responseContent;
  pageNumber: number;
  totalSize: number | null;
  maxPageNumber: number | null;
  trainingDataUploadDialogue: boolean;
  headers: DataTableHeader[];
  options: DataTableOptions;
  loading: boolean;
  selectedItem: Row[];
};

export default Vue.extend({
  props: {
    isSelection: {
      type: Boolean as PropType<boolean>,
      default: false,
    },
    value: {
      type: Number as PropType<number | null>,
      default: null,
    },
  },
  data(): Data {
    const headers: DataTableHeader[] = [
      { text: "id", value: "id", sortable: false },
      { text: "filename", value: "basename", sortable: false },
      { text: "file size", value: "filesize", sortable: false },
      { text: "upload date", value: "ins_datetime", sortable: false },
    ];
    if (!this.isSelection) {
      headers.push({ text: "", value: "actions", sortable: false });
    }
    return {
      trainingData: undefined,
      pageNumber: 1,
      totalSize: null,
      maxPageNumber: null,
      trainingDataUploadDialogue: false,
      headers,
      options: {
        page: 1,
        itemsPerPage: 5,
      },
      loading: false,
      selectedItem: [],
    };
  },
  computed: {
    uploadCaption(): string | null {
      if (AuthModule.currentProjectDetail === null) return null;
      let result = `Columns "${AuthModule.currentProjectDetail.user_column}" | "${AuthModule.currentProjectDetail.item_column}"`;
      if (AuthModule.currentProjectDetail.time_column) {
        result = `${result} | "${AuthModule.currentProjectDetail.time_column}"`;
      }
      result = `${result} are required.`;
      return result;
    },
    projectId(): number | null {
      return AuthModule.currentProjectId;
    },
  },
  watch: {
    options: {
      deep: true,
      async handler() {
        await this.fetchData();
      },
    },
    async trainingDataUploadDialogue() {
      await this.fetchData();
    },
    selectedItem(nv: Row[]): void {
      if (nv.length === 0) {
        null;
        this.$emit("input", null);
      } else {
        this.$emit("input", nv[0].id);
      }
    },
  },
  methods: {
    prettyFileSize(value: number | null) {
      return prettyFileSize(value);
    },
    clickRow(item: any) {
      if (this.isSelection) return;
      this.$router.push({
        name: "data-detail",
        params: { dataId: item.id },
      });
    },
    prettifyDate(x: string) {
      return prettifyDate(x);
    },
    async fetchData() {
      if (this.projectId === null) {
        return;
      }
      this.loading = true;
      let trainingData = await getWithRefreshToken<responseType>(
        AuthModule,
        trainingDataListURL +
          `?${qs.stringify({
            project: this.projectId,
            page: this.options.page,
            page_size: this.options.itemsPerPage,
          })}`
      );
      this.loading = false;

      if (trainingData?.results !== undefined) {
        this.trainingData = trainingData.results;
        this.totalSize = trainingData.count || null;
      }
    },
  },
  async mounted() {
    await this.fetchData();
  },
  components: {
    DataUpload,
  },
});
</script>
