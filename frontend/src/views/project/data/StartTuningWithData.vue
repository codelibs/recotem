<template>
  <div>
    <v-stepper v-model="step">
      <v-stepper-header>
        <v-stepper-step step="1"> Split </v-stepper-step>
        <v-divider></v-divider>
        <v-stepper-step step="2"> Evaluation </v-stepper-step>
        <v-divider></v-divider>
        <v-stepper-step step="3"> Job Configuration </v-stepper-step>
      </v-stepper-header>
      <v-stepper-content step="1" class="pt-2">
        <SplitConfigForm v-model="splitConfig">
          <template v-slot="{ isValid }">
            <v-row>
              <v-col cols="6"></v-col>
              <v-col cols="6">
                <v-btn :disabled="!isValid" @click="step = 2" color="info">
                  Continue <v-icon> mdi-arrow-right</v-icon>
                </v-btn>
              </v-col>
            </v-row>
          </template>
        </SplitConfigForm>
      </v-stepper-content>
      <v-stepper-content step="2">
        <EvaluationConfigForm v-model="evaluationConfig">
          <template v-slot="{ isValid }">
            <div class="d-flex justify-center mt-8">
              <div>
                <v-btn class="mr-2" @click="step = 1">
                  <v-icon>mdi-arrow-left</v-icon>Previous
                </v-btn>
                <v-btn
                  class="ml-2"
                  :disabled="!isValid"
                  @click="step = 3"
                  color="info"
                >
                  Continue <v-icon>mdi-arrow-right</v-icon>
                </v-btn>
              </div>
            </div>
          </template>
        </EvaluationConfigForm>
      </v-stepper-content>
      <v-stepper-content step="3">
        <JobConfigForm v-model="jobConfig" v-slot="{ isValid }">
          <div class="d-flex justify-center mt-8">
            <div>
              <v-btn class="mr-2" @click="step = 2">
                <v-icon>mdi-arrow-left</v-icon>Previous
              </v-btn>
              <v-btn
                class="ml-2"
                :disabled="!isValid"
                @click="createJob"
                color="info"
              >
                <v-icon>mdi-tune</v-icon>Start The job
              </v-btn>
            </div>
          </div>
        </JobConfigForm>
      </v-stepper-content>
    </v-stepper>
  </div>
</template>
<script lang="ts">
import Vue from "vue";
import { AuthModule } from "@/store/auth";
import { postWithRefreshToken, logout } from "@/utils/request";

import SplitConfigForm, {
  ResultType as SplitConfigResultType,
} from "@/components/tuning_steps/SetupSplitConfig.vue";

import EvaluationConfigForm, {
  ResultType as EvaluationConfigResultType,
} from "@/components/tuning_steps/SetupEvaluationMetric.vue";
import JobConfigForm, {
  ResultType as JobConfigResultType,
} from "@/components/tuning_steps/SetupTuningJob.vue";
import { paths } from "@/api/schema";

const splitConfigURL = "/api/split_config/";
type SplitConfigResponse =
  paths["/api/split_config/"]["post"]["responses"]["201"]["content"]["application/json"];

const evaluationConfigURL = "/api/evaluation_config/";
type EvaluationConfigResponse =
  paths["/api/evaluation_config/"]["post"]["responses"]["201"]["content"]["application/json"];

const tuningJobURL = "/api/parameter_tuning_job/";
type TuningJobResponse =
  paths["/api/parameter_tuning_job/"]["post"]["responses"]["201"]["content"]["application/json"];

type TuningJobRequestBody = Omit<
  paths["/api/parameter_tuning_job/"]["post"]["requestBody"]["content"]["application/json"],
  "id" | "ins_datetime" | "task_links"
>;
type Data = {
  step: number;
  splitConfig: SplitConfigResultType;
  evaluationConfig: EvaluationConfigResultType;
  jobConfig: JobConfigResultType;
};
export default Vue.extend({
  data(): Data {
    return {
      step: 1,
      splitConfig: {},
      evaluationConfig: {},
      jobConfig: {},
    };
  },
  methods: {
    async createJob(): Promise<void> {
      console.log("start posting...");
      if (
        this.dataId === null ||
        this.splitConfig === null ||
        this.evaluationConfig === null
      ) {
        throw "Invalid state.";
      }

      let splitConfigId: number;
      if (typeof this.splitConfig === "number") {
        splitConfigId = this.splitConfig;
      } else {
        const createdSplitConfig = await postWithRefreshToken<
          SplitConfigResultType,
          SplitConfigResponse
        >(AuthModule, splitConfigURL, this.splitConfig);
        console.log("created split config:", createdSplitConfig);
        if (createdSplitConfig === null) {
          await logout(AuthModule, this.$router);
          throw "Log out";
        }
        splitConfigId = createdSplitConfig.id;
      }
      let evaluationConfigId: number;
      if (typeof this.evaluationConfig === "number") {
        evaluationConfigId = this.evaluationConfig;
      } else {
        const createdEvaluationConfig = await postWithRefreshToken<
          EvaluationConfigResultType,
          EvaluationConfigResponse
        >(AuthModule, evaluationConfigURL, this.evaluationConfig);
        console.log("created split config:", createdEvaluationConfig);
        if (createdEvaluationConfig === null) {
          await logout(AuthModule, this.$router);
          throw "Log out";
        }
        evaluationConfigId = createdEvaluationConfig.id;
      }

      const jobPostBody: TuningJobRequestBody = {
        data: this.dataId,
        split: splitConfigId,
        evaluation: evaluationConfigId,
        ...this.jobConfig,
      };

      const createdJob = await postWithRefreshToken<
        TuningJobRequestBody,
        TuningJobResponse
      >(AuthModule, tuningJobURL, jobPostBody);
      console.log("created job:", createdJob);
      if (createdJob !== null) {
        this.$router.push({
          name: "tuning-job-detail",
          params: { parameterTuningJobId: `${createdJob.id}` },
        });
      } else {
        alert("failed to start the job");
      }
    },
  },
  components: {
    SplitConfigForm,
    EvaluationConfigForm,
    JobConfigForm,
  },
  computed: {
    dataId(): number | null {
      try {
        return parseInt(this.$route.params.dataId);
      } catch {
        return null;
      }
    },
  },
});
</script>
