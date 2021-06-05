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
      <v-stepper-content step="1">
        <SplitConfigForm v-model="evaluationConfig" />
      </v-stepper-content>
    </v-stepper>
    {{ evaluationConfig }}
  </div>
</template>
<script lang="ts">
import Vue from "vue";
import SplitConfigForm, {
  ResultType as EResult,
} from "@/components/tuning_steps/SetupSplitConfig.vue";

type Data = {
  step: number;
  evaluationConfig: EResult;
};
export default Vue.extend({
  data(): Data {
    return {
      step: 1,
      evaluationConfig: null,
    };
  },
  components: {
    SplitConfigForm,
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
