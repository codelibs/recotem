<template>
  <v-container>
    <ValidationObserver v-slot="{ valid }">
      <div class="flex-grow-1">
        <div class="d-flex">
          <div>
            <v-radio-group class="mr-6" v-model="how">
              <v-radio :value="1" label="Use Default Values"> </v-radio>
              <v-radio :value="2" label="Manually Define"> </v-radio>
            </v-radio-group>
          </div>
          <v-divider vertical v-if="how !== 1"></v-divider>
          <v-container v-if="how == 2">
            <ValidationProvider
              name="Number of trials"
              rules="min_value_1:1|is_integral"
              v-slot="{ errors }"
            >
              <v-text-field
                label="Number of trials"
                type="number"
                v-model.number="customConfig.n_trials"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="Overall timeout"
              rules="min_value_1:1|is_integral"
              v-slot="{ errors }"
            >
              <v-text-field
                label="(Optional) Overall timeout"
                type="number"
                v-model.number="customConfig.timeout_overall"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="Single step timeout"
              rules="min_value_1:1|is_integral"
              v-slot="{ errors }"
            >
              <v-text-field
                label="(Optional) Single step timeout"
                type="number"
                v-model.number="customConfig.timeout_singlestep"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="Memory budget"
              rules="min_value_1:1|is_integral"
              v-slot="{ errors }"
            >
              <v-text-field
                label="Rough memory budget (in MB)."
                type="number"
                v-model.number="customConfig.memory_budget"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>

            <ValidationProvider
              name="Parallel tasks running"
              rules="min_value_1:1|is_integral"
              v-slot="{ errors }"
            >
              <v-text-field
                label="Number of Paralel tasks to be run."
                type="number"
                v-model.number="customConfig.n_tasks_parallel"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="Random seed"
              rules="min_value:0|is_integral"
              v-slot="{ errors }"
            >
              <v-text-field
                label="(Optional) Random seed"
                type="number"
                v-model.number="customConfig.random_seed"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
          </v-container>
        </div>
      </div>
      <slot v-bind:isValid="(how == 2 && valid) || how == 1"> </slot>
    </ValidationObserver>
  </v-container>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import { is_integral, max_value, min_value, min_value_1 } from "@/utils/rules";
import { numberInputValueToNumberOrNull } from "@/utils/conversion";
import { paths } from "@/api/schema";

type openAPIArg =
  paths["/api/parameter_tuning_job/"]["post"]["requestBody"]["content"]["application/json"];
export type ResultType = Omit<
  openAPIArg,
  | "data"
  | "split"
  | "evaluation"
  | "id"
  | "ins_datetime"
  | "upd_datetime"
  | "tuned_model"
  | "best_config"
  | "irspack_version"
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
};

type Data = {
  customConfig: createConfigArg;
  how: number;
};

extend("max_value", max_value);
extend("min_value", min_value);
extend("min_value_1", min_value_1);
extend("is_integral", is_integral);

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
      customConfig: {
        name: "",
        n_trials: 40,
        n_tasks_parallel: 1,
        memory_budget: 8000,
        timeout_overall: "",
        timeout_singlestep: "",
        random_seed: "",
        train_after_tuning: true,
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
      result.n_tasks_parallel = numberInputValueToNumberOrNull(
        this.customConfig.n_tasks_parallel
      );
      result.n_trials = numberInputValueToNumberOrNull(
        this.customConfig.n_trials
      );
      result.memory_budget = numberInputValueToNumberOrNull(
        this.customConfig.memory_budget
      );
      result.timeout_overall = numberInputValueToNumberOrNull(
        this.customConfig.timeout_overall
      );
      result.timeout_singlestep = numberInputValueToNumberOrNull(
        this.customConfig.timeout_singlestep
      );
      result.random_seed = numberInputValueToNumberOrNull(
        this.customConfig.random_seed
      );
      result.train_after_tuning = this.customConfig.train_after_tuning;
      return result;
    },
  },
  components: {
    ValidationObserver,
    ValidationProvider,
  },
});
</script>
