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
                <v-btn :disabled="!isValid" @click="step = 2">
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
                <v-btn class="mr-2" @click="step = 1" color="info">
                  <v-icon>mdi-arrow-left</v-icon>Previous
                </v-btn>
                <v-btn class="ml-2" :disabled="!isValid" @click="step = 3">
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
              <v-btn class="mr-2" @click="step = 2" color="info">
                <v-icon>mdi-arrow-left</v-icon>Previous
              </v-btn>
              <v-btn
                class="ml-2"
                :disabled="!isValid"
                @click="step = 3"
                color="primary"
              >
                <v-icon>mdi-tune</v-icon>Start The job
              </v-btn>
            </div>
          </div>
        </JobConfigForm>
      </v-stepper-content>
    </v-stepper>
    <div>
      {{ splitConfig }}
    </div>
    <div>
      {{ evaluationConfig }}
    </div>
    <div>
      {{ jobConfig }}
    </div>
  </div>
</template>
<script lang="ts">
import Vue from "vue";
import SplitConfigForm, {
  ResultType as SplitConfigResultType,
} from "@/components/tuning_steps/SetupSplitConfig.vue";

import EvaluationConfigForm, {
  ResultType as EvaluationConfigResultType,
} from "@/components/tuning_steps/SetupEvaluationMetric.vue";
import JobConfigForm, {
  ResultType as JobConfigResultType,
} from "@/components/tuning_steps/SetupTuningJob.vue";

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
