<style lang="css" scoped>
.row-pointer >>> tbody tr :hover {
  cursor: pointer;
}
</style>

<template>
  <div>
    <v-data-table
      v-if="projectId !== null && models !== null"
      :items="models"
      disable-sort
      :headers="headers"
      :server-items-length="totalCount"
      :options.sync="options"
      @click:row="
        (item) =>
          $router.push({
            name: 'trained-model-detail',
            params: { trainedModelId: item.id },
          })
      "
      class="row-pointer"
      data-table-name="trained-model-list"
    >
      <template v-slot:[`item.filesize`]="{ value }">
        {{ prettyFileSize(value) }}
      </template>
      <template v-slot:[`item.ins_datetime`]="{ value }">
        <span class="text-caption">{{ prettifyDate(value) }} </span>
      </template>
      <template v-slot:[`item.task_links`]="{ value }">
        <JobStatus :tasks="value" />
      </template>
    </v-data-table>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { paths } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";
import { prettyFileSize } from "@/utils/conversion";
import { prettifyDate } from "@/utils/date";

import JobStatus from "@/components/TuningJobStatus.vue";
import { DataTableHeader, DataTableOptions } from "@/utils/table";

import qs from "qs";

const trainedModelListURL = "/api/trained_model/";
type APIResultType =
  paths["/api/trained_model/"]["get"]["responses"]["200"]["content"]["application/json"];
type TrainedModelArray = APIResultType["results"];

type Data = {
  headers: DataTableHeader[];
  models: TrainedModelArray | null;
  totalCount: number | null | undefined;
  pollingStop: boolean;
  options: DataTableOptions;
  loading: boolean;
};

const pageSize = 5;

function sleep(msec: number) {
  return new Promise((resolve: any) => setTimeout(resolve, msec));
}

export default Vue.extend({
  props: {
    externalCondition: {
      type: Object as PropType<Record<string, string>>,
      default: Object,
    },
  },
  data(): Data {
    return {
      models: null,
      totalCount: null,
      pollingStop: false,
      headers: [
        { text: "id", value: "id", sortable: false },
        { text: "Filename", value: "basename", sortable: false },
        { text: "File size", value: "filesize", sortable: false },
        { text: "Created", value: "ins_datetime", sortable: false },
        { text: "Status", value: "task_links", sortable: false },
      ],
      options: {
        page: 1,
        itemsPerPage: 5,
      },
      loading: false,
    };
  },
  beforeDestroy() {
    this.pollingStop = true;
  },
  methods: {
    prettyFileSize(value: number | null) {
      return prettyFileSize(value);
    },
    prettifyDate(x: string) {
      return prettifyDate(x);
    },

    async polling(): Promise<void> {
      for (;;) {
        await sleep(2000);
        await this.fetchData();
        if (this.pollingStop) {
          break;
        }
      }
    },
    async fetchData(): Promise<void> {
      let queryString = qs.stringify({
        page_size: this.options.itemsPerPage,
        page: this.options.page,
        ...this.externalCondition,
        data_loc__project: this.projectId,
      });
      this.loading = true;
      const result = await getWithRefreshToken<APIResultType>(
        AuthModule,
        `${trainedModelListURL}?${queryString}`
      );
      this.loading = false;
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
    await this.polling();
  },
  components: {
    JobStatus,
  },
});
</script>
