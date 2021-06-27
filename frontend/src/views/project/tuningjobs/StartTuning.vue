<template>
  <div>
    <div>Setup tuning job:</div>
    <v-stepper v-model="step">
      <v-stepper-header>
        <v-stepper-step step="1"> Data </v-stepper-step>
        <v-divider></v-divider>
        <v-stepper-step step="2"> Split </v-stepper-step>
        <v-divider></v-divider>
        <v-stepper-step step="3"> Evaluation </v-stepper-step>
        <v-divider></v-divider>
        <v-stepper-step step="4"> Job Configuration </v-stepper-step>
      </v-stepper-header>
      <v-stepper-content step="1" class="pt-2">
        <TrainingDataList isSelection v-model="dataId" />
        <v-row>
          <v-col cols="6" />
          <v-col cols="6">
            <v-btn :disabled="dataId === null" @click="step = 2" color="info">
              Continue <v-icon> mdi-arrow-right</v-icon>
            </v-btn>
          </v-col>
        </v-row>
      </v-stepper-content>

      <v-stepper-content step="2" class="pt-2">
        <SplitConfigForm v-model="splitConfig">
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
        </SplitConfigForm>
      </v-stepper-content>
      <v-stepper-content step="3">
        <EvaluationConfigForm v-model="evaluationConfig">
          <template v-slot="{ isValid }">
            <div class="d-flex justify-center mt-8">
              <div>
                <v-btn class="mr-2" @click="step = 2">
                  <v-icon>mdi-arrow-left</v-icon>Previous
                </v-btn>
                <v-btn
                  class="ml-2"
                  :disabled="!isValid"
                  @click="step = 4"
                  color="info"
                >
                  Continue <v-icon>mdi-arrow-right</v-icon>
                </v-btn>
              </div>
            </div>
          </template>
        </EvaluationConfigForm>
      </v-stepper-content>
      <v-stepper-content step="4">
        <JobConfigForm v-model="jobConfig" v-slot="{ isValid }">
          <div class="d-flex justify-center mt-8">
            <div>
              <v-btn class="mr-2" @click="step = 3">
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
import {
  createSplitConfigIfNeeded,
  createEvaluationConfigIfNeeded,
  createParameterTuningJob,
} from "@/views/project/data/StartTuningWithData.vue";

import SplitConfigForm, {
  ResultType as SplitConfigResultType,
} from "@/components/tuning_steps/SetupSplitConfig.vue";

import EvaluationConfigForm, {
  ResultType as EvaluationConfigResultType,
} from "@/components/tuning_steps/SetupEvaluationMetric.vue";
import JobConfigForm, {
  ResultType as JobConfigResultType,
} from "@/components/tuning_steps/SetupTuningJob.vue";

import TrainingDataList from "@/components/TrainingDataList.vue";

type Data = {
  step: number;
  dataId: number | null;
  splitConfig: SplitConfigResultType;
  evaluationConfig: EvaluationConfigResultType;
  jobConfig: JobConfigResultType;
};
export default Vue.extend({
  data(): Data {
    return {
      step: 1,
      dataId: null,
      splitConfig: {},
      evaluationConfig: {},
      jobConfig: {},
    };
  },
  methods: {
    async createJob(): Promise<void> {
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
    TrainingDataList,
  },
  computed: {},
});
</script>
