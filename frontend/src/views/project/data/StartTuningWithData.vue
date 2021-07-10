<template>
  <div>
    <div>Setup tuning job:</div>

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
import Router from "vue-router";
import { AuthModule, Auth } from "@/store/auth";
import { postWithRefreshToken } from "@/utils/request";

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

export async function createSplitConfigIfNeeded(
  auth: Auth,
  splitConfig: SplitConfigResultType
): Promise<number> {
  if (typeof splitConfig === "number") {
    return splitConfig;
  } else {
    const createdSplitConfig = await postWithRefreshToken<
      SplitConfigResultType,
      SplitConfigResponse
    >(auth, splitConfigURL, splitConfig);
    if (createdSplitConfig === null) {
      throw "Log out";
    }
    return createdSplitConfig.id;
  }
}

const evaluationConfigURL = "/api/evaluation_config/";
type EvaluationConfigResponse =
  paths["/api/evaluation_config/"]["post"]["responses"]["201"]["content"]["application/json"];
export async function createEvaluationConfigIfNeeded(
  auth: Auth,
  evaluationConfig: EvaluationConfigResultType
): Promise<number> {
  let evaluationConfigId: number;
  if (typeof evaluationConfig === "number") {
    return evaluationConfig;
  } else {
    const createdEvaluationConfig = await postWithRefreshToken<
      EvaluationConfigResultType,
      EvaluationConfigResponse
    >(auth, evaluationConfigURL, evaluationConfig);
    if (createdEvaluationConfig === null) {
      throw "Log out";
    }
    return createdEvaluationConfig.id;
  }
}
const tuningJobURL = "/api/parameter_tuning_job/";
type TuningJobResponse =
  paths["/api/parameter_tuning_job/"]["post"]["responses"]["201"]["content"]["application/json"];

type TuningJobRequestBody = Omit<
  paths["/api/parameter_tuning_job/"]["post"]["requestBody"]["content"]["application/json"],
  "id" | "ins_datetime" | "task_links"
>;

export async function createParameterTuningJob(
  auth: Auth,
  router: Router,
  data: number,
  split: number,
  evaluation: number,
  jobConfig: JobConfigResultType
): Promise<void> {
  const jobPostBody: TuningJobRequestBody = {
    data,
    split,
    evaluation,
    ...jobConfig,
  };

  const createdJob = await postWithRefreshToken<
    TuningJobRequestBody,
    TuningJobResponse
  >(auth, tuningJobURL, jobPostBody);
  if (createdJob !== null) {
    router.push({
      name: "tuning-job-detail",
      params: { parameterTuningJobId: `${createdJob.id}` },
    });
  } else {
    alert("failed to start the job");
  }
}

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

      let splitConfigId = await createSplitConfigIfNeeded(
        AuthModule,
        this.splitConfig
      );
      let evaluationConfigId = await createEvaluationConfigIfNeeded(
        AuthModule,
        this.evaluationConfig
      );
      await createParameterTuningJob(
        AuthModule,
        this.$router,
        this.dataId,
        splitConfigId,
        evaluationConfigId,
        this.jobConfig
      );
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
