<template>
  <v-container>
    <ValidationObserver v-slot="{ valid }">
      <div class="d-flex">
        <div>
          <v-radio-group class="mr-6" v-model="how">
            <v-radio :value="1" name="use-default" label="Use Default Values">
            </v-radio>
            <v-radio :value="2" name="manually-define" label="Manually Define">
            </v-radio>
          </v-radio-group>
        </div>
        <v-divider vertical v-if="how !== 1"></v-divider>
        <v-container v-if="how == 2" class="ml-4 mt-0 pt-0">
          <ValidationProvider
            name="Number of trials"
            rules="isPositiveInteger"
            v-slot="{ errors }"
          >
            <v-text-field
              label="Number of trials"
              type="number"
              name="n_trials"
              v-model.number="customConfig.n_trials"
              :error-messages="errors"
            ></v-text-field>
          </ValidationProvider>
          <ValidationProvider
            name="Overall timeout"
            rules="isPositiveInteger"
            v-slot="{ errors }"
          >
            <v-text-field
              name="timeout_overall"
              label="Overall timeout"
              type="number"
              v-model.number="customConfig.timeout_overall"
              :error-messages="errors"
            ></v-text-field>
          </ValidationProvider>
          <ValidationProvider
            name="Single step timeout"
            rules="isPositiveInteger"
            v-slot="{ errors }"
          >
            <v-text-field
              label="Single step timeout"
              name="timeout_singlestep"
              type="number"
              v-model.number="customConfig.timeout_singlestep"
              :error-messages="errors"
            ></v-text-field>
          </ValidationProvider>
          <ValidationProvider
            name="Memory budget"
            rules="isPositiveInteger"
            v-slot="{ errors }"
          >
            <v-text-field
              label="Rough memory budget (in MB)."
              name="memory_budget"
              type="number"
              v-model.number="customConfig.memory_budget"
              :error-messages="errors"
            ></v-text-field>
          </ValidationProvider>

          <ValidationProvider
            name="Parallel tasks running"
            rules="isPositiveInteger"
            v-slot="{ errors }"
          >
            <v-text-field
              label="Number of Paralel tasks to be run."
              name="n_tasks_parallel"
              type="number"
              v-model.number="customConfig.n_tasks_parallel"
              :error-messages="errors"
            ></v-text-field>
          </ValidationProvider>
          <ValidationProvider
            name="Random seed"
            rules="isNonnegativeInteger"
            v-slot="{ errors }"
          >
            <v-text-field
              label="Random seed"
              name="random_seed"
              type="number"
              v-model.number="customConfig.random_seed"
              :error-messages="errors"
            ></v-text-field>
          </ValidationProvider>
          <v-select
            multiple
            chips
            v-model="customConfig.tried_algorithms"
            :items="algoChoices"
          >
          </v-select>
          <v-checkbox
            v-model="customConfig.train_after_tuning"
            label="Train a model using the full data using the tuned configuration."
          ></v-checkbox>
        </v-container>
      </div>
      <slot v-bind:isValid="(how == 2 && valid) || how == 1"> </slot>
    </ValidationObserver>
  </v-container>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import {
  isInteger,
  isPositiveInteger,
  validRatio,
  isNonnegativeInteger,
} from "@/utils/rules";
import { numberInputValueToNumberOrUndefined } from "@/utils/conversion";
import { paths } from "@/api/schema";

type openAPIArg =
  paths["/api/parameter_tuning_job/"]["post"]["requestBody"]["content"]["application/json"];
export type ResultType = Omit<
  openAPIArg,
  | "id"
  | "data"
  | "split"
  | "evaluation"
  | "ins_datetime"
  | "tuned_model"
  | "best_config"
  | "irspack_version"
  | "task_links"
>;

type createConfigArg = {
  name: string | undefined;
  n_tasks_parallel: number | string; //(default=1)
  n_trials: number | string; //(default=40)
  memory_budget: number | string; // (default=8000)
  timeout_overall: number | string;
  timeout_singlestep: number | string;
  random_seed: number | string;
  train_after_tuning: boolean;
  tried_algorithms: string[];
};

type Data = {
  customConfig: createConfigArg;
  algoChoices: string[];
  how: number;
};
const algoChoices = [
  "DenseSLIM",
  "SLIM",
  "IALS",
  "AsymmetricCosineKNN",
  "RP3beta",
  "AsymmetricCosineUserKNN",
  "TopPop",
  "TruncatedSVD",
];

extend("isInteger", isInteger);
extend("isPositiveInteger", isPositiveInteger);
extend("isNonnegativeInteger", isNonnegativeInteger);
extend("validRatio", validRatio);

export default Vue.extend({
  props: {
    value: {
      type: Object as PropType<ResultType>,
      required: false,
    },
  },
  data(): Data {
    return {
      how: 1,
      algoChoices,
      customConfig: {
        name: "",
        n_trials: 40,
        n_tasks_parallel: 1,
        memory_budget: 8000,
        timeout_overall: "",
        timeout_singlestep: "",
        random_seed: "",
        train_after_tuning: true,
        tried_algorithms: [
          "DenseSLIM",
          "SLIM",
          "IALS",
          "AsymmetricCosineKNN",
          "RP3beta",
        ],
      },
    };
  },
  methods: {},
  async mounted() {
    this.$emit("input", {});
  },
  watch: {
    result: {
      deep: true,
      handler: async function (nv) {
        this.$emit("input", nv);
      },
    },
  },
  computed: {
    result(): ResultType {
      let result: ResultType = {};
      if (this.how == 2) {
        result.n_tasks_parallel = numberInputValueToNumberOrUndefined(
          this.customConfig.n_tasks_parallel
        );
        result.n_trials = numberInputValueToNumberOrUndefined(
          this.customConfig.n_trials
        );
        result.memory_budget = numberInputValueToNumberOrUndefined(
          this.customConfig.memory_budget
        );
        result.timeout_overall = numberInputValueToNumberOrUndefined(
          this.customConfig.timeout_overall
        );
        result.timeout_singlestep = numberInputValueToNumberOrUndefined(
          this.customConfig.timeout_singlestep
        );
        result.random_seed = numberInputValueToNumberOrUndefined(
          this.customConfig.random_seed
        );
        result.train_after_tuning = this.customConfig.train_after_tuning;
        result.tried_algorithms_json = JSON.stringify(
          this.customConfig.tried_algorithms
        );
      }
      return result;
    },
  },
  components: {
    ValidationObserver,
    ValidationProvider,
  },
});
</script>
