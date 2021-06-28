<template>
  <div>
    <v-data-table
      v-if="models !== null"
      :items="models"
      disable-sort
      :dense="isSelection"
      :single-select="isSelection"
      :show-select="isSelection"
      :single-expand="isSelection"
      show-expand
      :headers="headers"
      :server-items-length="totalCount"
      :options.sync="options"
    >
      <template v-slot:[`item.ins_datetime`]="{ value }">
        <span class="text-caption">{{ prettifyDate(value) }} </span>
      </template>
      <template v-slot:[`item.name`]="{ item, value }">
        <span v-if="item.tuning_job !== null">
          <v-btn
            icon
            :to="{
              name: 'tuning-job-detail',
              params: { parameterTuningJobId: item.tuning_job },
            }"
          >
            <v-icon>mdi-tune</v-icon>
          </v-btn>
          Result of tuning job {{ item.tuning_job }}
        </span>
        <span v-else> {{ value }}</span>
      </template>
      <template v-slot:expanded-item="{ headers, item }">
        <td :colspan="headers.length" class="pa-4">
          <ModelConfigView :modelConfigId="item.id" />
        </td>
      </template>
    </v-data-table>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { paths, components } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";
import { logout } from "@/utils/request";
import { prettyFileSize } from "@/utils/conversion";
import { prettifyDate } from "@/utils/date";

import { DataTableHeader, DataTableOptions } from "@/utils/table";
import ModelConfigView from "@/components/ModelConfigView.vue";

import qs from "qs";

const trainedModelListURL = "/api/model_configuration/";
type APIResultType =
  paths["/api/model_configuration/"]["get"]["responses"]["200"]["content"]["application/json"];
type ModelConfigurationArray = APIResultType["results"];

type Data = {
  headers: DataTableHeader[];
  models: ModelConfigurationArray | null;
  totalCount: number | null | undefined;
  options: DataTableOptions;
  loading: boolean;
};

export default Vue.extend({
  props: {
    value: {
      type: Number as PropType<number | null>,
      default: null,
    },
    externalCondition: {
      type: Object as PropType<Record<string, string>>,
      default: Object,
    },
    isSelection: {
      type: Boolean as PropType<boolean>,
      default: true,
    },
  },
  data(): Data {
    const headers = [
      { text: "id", value: "id", sortable: false },
      { text: "Created", value: "ins_datetime", sortable: false },
      {
        text: "Recommender Class",
        value: "recommender_class_name",
        sortable: false,
      },
      { text: "", value: "name", sortable: false },
    ];
    return {
      models: null,
      totalCount: null,
      headers,
      options: {
        page: 1,
        itemsPerPage: 5,
      },
      loading: false,
    };
  },
  methods: {
    prettyFileSize(value: number | null) {
      return prettyFileSize(value);
    },
    prettifyDate(x: string) {
      return prettifyDate(x);
    },
    async fetchData(): Promise<void> {
      let queryString = qs.stringify({
        page_size: this.options.itemsPerPage,
        page: this.options.page,
        ...this.externalCondition,
        project: this.projectId,
      });
      this.loading = true;
      const result = await getWithRefreshToken<APIResultType>(
        AuthModule,
        `${trainedModelListURL}?${queryString}`
      );
      this.loading = false;
      if (result === null) {
        await logout(AuthModule, this.$router);
        throw "logout";
      }
      this.totalCount = result.count || 0;
      this.models = result.results || [];
    },
  },
  computed: {
    projectId(): number | null {
      return AuthModule.currentProjectId;
    },
  },
  watch: {
    options: {
      deep: true,
      async handler(): Promise<void> {
        await this.fetchData();
      },
    },
    externalCondition: {
      deep: true,
      async handler(): Promise<void> {
        this.options.page = 1;
        await this.fetchData();
      },
    },
  },
  async mounted() {
    await this.fetchData();
  },
  components: { ModelConfigView },
});
</script>
