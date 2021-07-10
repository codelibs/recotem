<style lang="css" scoped>
.row-pointer >>> tbody tr :hover {
  cursor: pointer;
}
</style>

<template>
  <div v-if="tuningJobs !== null && tuningJobs !== undefined">
    <v-data-table
      class="row-pointer"
      :options.sync="options"
      :items="tuningJobs"
      @click:row="
        (item) =>
          $router.push({
            name: 'tuning-job-detail',
            params: { parameterTuningJobId: item.id },
          })
      "
      :headers="headers"
      :server-items-length="totalCount"
    >
      <template v-slot:[`item.id`]="{ value }">
        <td>{{ value }}</td>
      </template>
      <template v-slot:[`item.ins_datetime`]="{ value }">
        <td>{{ prettifyDate(value) }}</td>
      </template>
      <template v-slot:[`item.task_links`]="{ value }">
        <td>
          <TuningJobStatus :tasks="value" />
        </td>
      </template>
      <template v-slot:[`item.trained_model`]="{ item }">
        <td>
          <v-btn
            icon
            v-if="typeof item.tuned_model == 'number'"
            :to="{
              name: 'trained-model-detail',
              params: { trainedModelId: item.tuned_model },
            }"
          >
            <v-icon color="green"> mdi-calculator </v-icon>
          </v-btn>
        </td>
      </template>
    </v-data-table>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { paths, components } from "@/api/schema";
import { DataTableOptions, DataTableHeader } from "@/utils/table";
import { getWithRefreshToken } from "@/utils/request";
import { prettifyDate } from "@/utils/date";
import { AuthModule } from "@/store/auth";
import TuningJobStatus from "@/components/TuningJobStatus.vue";
import qs from "qs";

const tuningJobListURL = "/api/parameter_tuning_job/";
type APIResultType =
  paths["/api/parameter_tuning_job/"]["get"]["responses"]["200"]["content"]["application/json"];
type TuningJobArray = APIResultType["results"];

type Data = {
  page: number;
  tuningJobs: TuningJobArray | null;
  totalCount: number | null | undefined;
  pollingStop: boolean;
  options: DataTableOptions;
  headers: DataTableHeader[];
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
      page: 1,
      tuningJobs: null,
      totalCount: null,
      pollingStop: false,
      options: {
        page: 1,
        itemsPerPage: 10,
      },
      headers: [
        { text: "id", value: "id", sortable: false },
        { text: "Started on", value: "ins_datetime", sortable: false },
        { text: "Status", value: "task_links", sortable: false },
        { text: "Trained model", value: "trained_model", sortable: false },
      ],
    };
  },
  beforeDestroy() {
    this.pollingStop = true;
  },
  methods: {
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
      });
      const result = await getWithRefreshToken<APIResultType>(
        AuthModule,
        `${tuningJobListURL}?${queryString}`
      );
      this.totalCount = result.count || 0;
      this.tuningJobs = result.results || [];
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
        this.page = 1;
        await this.fetchData();
      },
    },
  },
  async mounted() {
    await this.fetchData();
    await this.polling();
  },
  components: {
    TuningJobStatus,
  },
});
</script>
