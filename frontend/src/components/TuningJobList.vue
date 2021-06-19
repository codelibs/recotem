<template>
  <div>
    <template v-if="tuningJobs !== null">
      <template v-if="tuningJobs !== undefined && tuningJobs.length > 0">
        <v-list>
          <template v-for="(td, i) in tuningJobs">
            <v-list-item
              :key="i"
              :to="{
                name: 'tuning-job-detail',
                params: { parameterTuningJobId: td.id },
              }"
            >
              <v-list-item-content>
                <v-row>
                  <v-col cols="2">
                    <v-list-item-title> id: {{ td.id }} </v-list-item-title>
                  </v-col>
                  <v-col cols="6">
                    <v-list-item-subtitle>
                      {{ td.ins_datetime }}
                    </v-list-item-subtitle>
                  </v-col>
                  <v-col cols="2">
                    <TuningJobStatus :tasks="td.task_links" />
                  </v-col>
                </v-row>
              </v-list-item-content>
              <v-list-item-action>
                <v-btn
                  icon
                  v-if="typeof td.tuned_model == 'number'"
                  :to="{
                    name: 'trained-model-detail',
                    params: { trainedModelId: td.tuned_model },
                  }"
                >
                  <v-icon color="green"> mdi-calculator </v-icon>
                </v-btn>
              </v-list-item-action>
            </v-list-item>
            <v-divider :key="i + 0.5"></v-divider>
          </template>
        </v-list>
        <v-pagination
          v-if="maxPageSize !== null && maxPageSize > 1"
          v-model="page"
          :length="maxPageSize"
        >
        </v-pagination>
      </template>
      <div v-else class="ma-8 text-h6 text-center">No tuning job yet</div>
    </template>
    <div v-else class="ma-8 text-h5 text-center">loading...</div>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { paths, components } from "@/api/schema";
import { computeMaxPage } from "@/utils/pagination";
import { getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";
import { logout } from "@/utils/request";
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
    };
  },
  beforeDestroy() {
    this.pollingStop = true;
  },
  methods: {
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
        page_size: pageSize,
        page: this.page,
        ...this.externalCondition,
      });
      console.log(queryString);
      const result = await getWithRefreshToken<APIResultType>(
        AuthModule,
        `${tuningJobListURL}?${queryString}`
      );
      console.log(result);
      if (result === null) {
        await logout(AuthModule, this.$router);
        throw "logout";
      }
      this.totalCount = result.count || 0;
      this.tuningJobs = result.results || [];
    },
  },
  computed: {
    maxPageSize(): number | null {
      return computeMaxPage(
        this.totalCount === undefined ? null : this.totalCount,
        pageSize
      );
    },
  },
  watch: {
    async page(): Promise<void> {
      await this.fetchData();
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
