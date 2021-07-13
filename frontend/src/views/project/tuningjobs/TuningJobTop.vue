<style scoped>
table.config th {
  padding-right: 40px;
  text-align: left;
}
table.config tr {
  padding: 5px;
  height: 32px;
}
pre-wrap {
  white-space: pre-wrap !important;
}
</style>
<template>
  <div v-if="tuningJobBasicInfo !== null" class="pa-4">
    <div class="d-flex align-end pl-4 pr-4 mb-2">
      <div class="text-h5">Tuning Job {{ tuningJobBasicInfo.id }}</div>
      <div class="text-caption ml-4">{{ tuningJobBasicInfo.ins_datetime }}</div>
      <div class="flex-grow-1"></div>
      <TuningJobStatus
        v-model="jobComplete"
        :tasks="tuningJobBasicInfo.task_links"
      />
    </div>
    <v-expansion-panels v-model="panel" multiple inset>
      <v-expansion-panel>
        <v-expansion-panel-header>
          <div>Configuration</div>
        </v-expansion-panel-header>
        <v-expansion-panel-content>
          <table class="config pa-4">
            <tbody>
              <tr>
                <th>Data</th>
                <td v-if="dataDetail !== null">
                  {{ dataDetail.basename }}
                </td>
                <td v-else>
                  <div class="text-caption">deleted</div>
                </td>
                <td>
                  <v-btn
                    v-if="dataDetail !== null && dataDetail.filesize !== null"
                    icon
                    color="primary"
                    :to="{
                      name: 'data-detail',
                      params: { dataId: dataDetail.id },
                    }"
                  >
                    <v-icon>mdi-folder</v-icon>
                  </v-btn>
                </td>
              </tr>
              <tr>
                <th>Trials</th>
                <td>{{ tuningJobBasicInfo.n_trials }}</td>
              </tr>
              <tr>
                <th>Train after tuning</th>
                <td>{{ tuningJobBasicInfo.train_after_tuning }}</td>
              </tr>
              <tr>
                <th>Overall timeout</th>
                <td>
                  {{ numberToDisplay(tuningJobBasicInfo.timeout_overall) }}
                </td>
              </tr>
              <tr>
                <th>Single step timeout</th>
                <td>
                  {{ numberToDisplay(tuningJobBasicInfo.timeout_singlestep) }}
                </td>
              </tr>
              <tr>
                <th>Memory budget</th>
                <td>
                  {{ numberToDisplay(tuningJobBasicInfo.memory_budget) }} MB
                </td>
              </tr>
              <tr>
                <th>Random seed</th>
                <td>{{ numberToDisplay(tuningJobBasicInfo.random_seed) }}</td>
              </tr>
              <tr>
                <th>Parallel tasks</th>
                <td>
                  {{ numberToDisplay(tuningJobBasicInfo.n_tasks_parallel) }}
                </td>
              </tr>
            </tbody>
          </table>
        </v-expansion-panel-content>
      </v-expansion-panel>
      <v-expansion-panel v-if="splitConfigDetail !== null">
        <v-expansion-panel-header> Split </v-expansion-panel-header>
        <v-expansion-panel-content>
          <SplitConfigView :splitConfigDetail="splitConfigDetail" />
        </v-expansion-panel-content>
      </v-expansion-panel>
      <v-expansion-panel v-if="evaluationConfigDetail !== null">
        <v-expansion-panel-header> Evaluation </v-expansion-panel-header>
        <v-expansion-panel-content>
          <EvaluationConfigView
            :evaluationConfigDetail="evaluationConfigDetail"
          />
        </v-expansion-panel-content>
      </v-expansion-panel>
      <v-expansion-panel
        v-if="jobComplete && typeof tuningJobBasicInfo.best_config === 'number'"
      >
        <v-expansion-panel-header> Results </v-expansion-panel-header>
        <v-expansion-panel-content>
          <table class="config">
            <tbody>
              <tr v-if="tuningJobBasicInfo.tuned_model !== null">
                <th>Trained Model</th>
                <td>
                  model-{{ tuningJobBasicInfo.tuned_model }}
                  <v-btn
                    icon
                    color="green"
                    :to="{
                      name: 'trained-model-detail',
                      params: {
                        trainedModelId: tuningJobBasicInfo.tuned_model,
                      },
                    }"
                    ><v-icon>mdi-calculator</v-icon></v-btn
                  >
                </td>
              </tr>
              <tr v-if="tuningJobBasicInfo.best_score">
                <th>Best score</th>
                <td>{{ tuningJobBasicInfo.best_score }}</td>
              </tr>
            </tbody>
          </table>
          <ModelConfigView :modelConfigId="tuningJobBasicInfo.best_config" />
        </v-expansion-panel-content>
      </v-expansion-panel>
      <v-expansion-panel>
        <v-expansion-panel-header> Logs </v-expansion-panel-header>
        <v-expansion-panel-content>
          <div>
            <v-alert
              v-for="(msg, i) in errors"
              :key="i"
              class="text-caption"
              type="error"
            >
              <div class="pre-wrap">{{ msg }}</div></v-alert
            >
          </div>

          <LogView
            :condition="{ tuning_job_id: tuningJobBasicInfo.id }"
            :complete="jobComplete"
          />
        </v-expansion-panel-content>
      </v-expansion-panel>
    </v-expansion-panels>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";
import TuningJobStatus from "@/components/TuningJobStatus.vue";
import SplitConfigView from "@/components/SplitConfigView.vue";
import EvaluationConfigView from "@/components/EvaluationConfigView.vue";
import LogView from "@/components/FetchLogs.vue";
import ModelConfigView from "@/components/ModelConfigView.vue";
import { AxiosError } from "axios";

const retrieveURL = "/api/parameter_tuning_job";
type JobDetailType =
  paths["/api/parameter_tuning_job/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];

type DataDetail =
  paths["/api/training_data/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];
const dataInfoURL = "/api/training_data/";

type SplitConfigDetail =
  paths["/api/split_config/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];
const splitConfigInfoURL = "/api/split_config/";

type EvaluationConfigDetail =
  paths["/api/evaluation_config/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];
const evaluationConfigInfoURL = "/api/evaluation_config";

type Data = {
  jobComplete: boolean;
  tuningJobBasicInfo: JobDetailType | null;
  dataDetail: DataDetail | null;
  panel: number[];
  splitConfigDetail: SplitConfigDetail | null;
  evaluationConfigDetail: EvaluationConfigDetail | null;
  pollingStop: boolean;
};
export default Vue.extend({
  data(): Data {
    return {
      jobComplete: false,
      tuningJobBasicInfo: null,
      dataDetail: null,
      panel: [0],
      splitConfigDetail: null,
      evaluationConfigDetail: null,
      pollingStop: false,
    };
  },
  async mounted(): Promise<void> {
    await this.fetchTuningJobDetail();
    if (this.tuningJobBasicInfo === null) return;
    try {
      let result = await getWithRefreshToken<DataDetail>(
        AuthModule,
        `${dataInfoURL}/${this.tuningJobBasicInfo.data}`,
        undefined,
        false
      );
      this.dataDetail = result;
    } catch (e) {
      if (e.response?.status != 404) {
        this.dataDetail = null;
      } else {
        throw e;
      }
    }

    const splitConfigDetal = await getWithRefreshToken<SplitConfigDetail>(
      AuthModule,
      `${splitConfigInfoURL}/${this.tuningJobBasicInfo.split}`
    );
    this.splitConfigDetail = splitConfigDetal;
    const evaluationConfigDetail =
      await getWithRefreshToken<EvaluationConfigDetail>(
        AuthModule,
        `${evaluationConfigInfoURL}/${this.tuningJobBasicInfo.evaluation}`
      );
    this.evaluationConfigDetail = evaluationConfigDetail;
    await this.polling();
  },
  beforeDestroy() {
    this.pollingStop = true;
  },

  watch: {
    async projectId() {
      await this.fetchTuningJobDetail();
    },
  },
  methods: {
    numberToDisplay(value: number | null | undefined): string {
      if (value === null || value === undefined) return "-";
      else return `${value}`;
    },
    async polling(): Promise<void> {
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        await this.fetchTuningJobDetail();
        if (this.pollingStop) {
          break;
        }
        if (this.jobComplete) {
          break;
        }
      }
    },

    async fetchTuningJobDetail(): Promise<void> {
      if (this.parameterTuningJobId == null) return;
      if (isNaN(this.parameterTuningJobId)) return;

      let result = await getWithRefreshToken<JobDetailType>(
        AuthModule,
        `${retrieveURL}/${this.parameterTuningJobId}`
      );
      if (result === null) return;
      this.tuningJobBasicInfo = result;
    },
    async fetchTrainingDataDetail(): Promise<void> {
      if (this.tuningJobBasicInfo === null) return;
    },
  },
  computed: {
    errors(): string[] {
      if (this.tuningJobBasicInfo === null) return [];
      let result: string[] = [];
      const tracebacks = this.tuningJobBasicInfo.task_links.map(
        (x) => x.task.traceback
      );
      for (let s of tracebacks) {
        if (s) result.push(s);
      }
      return result;
    },
    parameterTuningJobId(): number | null {
      try {
        return parseInt(this.$route.params.parameterTuningJobId);
      } catch {
        return null;
      }
    },
  },
  components: {
    TuningJobStatus,
    SplitConfigView,
    EvaluationConfigView,
    LogView,
    ModelConfigView,
  },
});
</script>
