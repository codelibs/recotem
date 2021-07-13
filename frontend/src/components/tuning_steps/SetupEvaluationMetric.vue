<template>
  <v-container>
    <ValidationObserver v-slot="{ valid }">
      <div class="d-flex">
        <div>
          <v-radio-group class="mr-6" v-model="how">
            <v-radio :value="1" name="use-default" label="Use Default Values">
            </v-radio>
            <v-radio
              name="use-preset"
              :disabled="existingConfigs.length == 0"
              :value="2"
              label="Use Preset Config"
            >
            </v-radio>
            <v-radio name="manually-define" :value="3" label="Manually Define">
            </v-radio>
          </v-radio-group>
        </div>
        <v-divider vertical v-if="how !== 1"></v-divider>

        <div class="flex-grow-1">
          <v-container v-if="how == 2" fluid class="pt-0 mt-0">
            <v-data-table
              :headers="[
                { text: 'id', value: 'id', sortable: false },
                { text: 'name', value: 'name', sortable: false },
                { text: 'created at', value: 'ins_datetime', sortable: false },
              ]"
              :items="existingConfigs"
              single-select
              single-expand
              show-expand
              dense
              show-select
              v-model="selectedConfigs"
              class="pa-4"
            >
              <template v-slot:top>
                <div class="text-caption">Preset configurations:</div>
              </template>
              <template v-slot:[`item.ins_datetime`]="{ value }">
                <td>{{ prettifyDate(value) }}</td>
              </template>
              <template v-slot:expanded-item="{ headers, item }">
                <td :colspan="headers.length" class="pa-4">
                  <EvaluationConfigView :evaluationConfigDetail="item" />
                </td>
              </template>
            </v-data-table>
          </v-container>
          <v-container v-if="how == 3" class="pl-8">
            <v-select
              :items="metricChoice"
              v-model="customConfig.target_metric"
            >
            </v-select>
            <ValidationProvider
              name="cutoff"
              rules="isPositiveInteger"
              v-slot="{ errors }"
            >
              <v-text-field
                label="Cutoff"
                name="cutoff"
                type="number"
                v-model.number="customConfig.cutoff"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="savename"
              rules="evaluationConfigNameExists"
              v-slot="{ errors }"
              :debounce="300"
            >
              <v-text-field
                label="(Optional) Save this config with name"
                name="savename"
                v-model="saveName"
                :error-messages="errors"
              >
              </v-text-field>
            </ValidationProvider>
          </v-container>
        </div>
      </div>
      <slot
        v-bind:isValid="
          (how == 3 && valid) || how == 1 || (how == 2 && selectedId !== null)
        "
      >
      </slot>
    </ValidationObserver>
  </v-container>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";
import { isPositiveInteger } from "@/utils/rules";
import { numberInputValueToNumberOrUndefined } from "@/utils/conversion";
import { prettifyDate } from "@/utils/date";
import EvaluationConfigView from "@/components/EvaluationConfigView.vue";

import { paths } from "@/api/schema";
import qs from "qs";

type ExistingConfigs =
  paths["/api/evaluation_config/"]["get"]["responses"]["200"]["content"]["application/json"];
const existingConfigsUrl = "/api/evaluation_config/";

type createConfigArg = Omit<
  paths["/api/evaluation_config/"]["post"]["requestBody"]["content"]["application/json"],
  "id" | "ins_datetime"
>;

type Data = {
  how: number;
  customConfig: createConfigArg;
  saveName: string;
  existingConfigs: ExistingConfigs;
  selectedConfigs: ExistingConfigs;
};
export type ResultType = createConfigArg | number | null;

extend("isPositiveInteger", isPositiveInteger);

extend("evaluationConfigNameExists", {
  async validate(value: string) {
    const result = await getWithRefreshToken<ExistingConfigs>(
      AuthModule,
      existingConfigsUrl + `?${qs.stringify({ name: value })}`
    );
    if (result?.length === 0) {
      return true;
    }
    return `A preset with this name already exists.`;
  },
});
type ValidMetricValues =
  paths["/api/evaluation_config/"]["post"]["requestBody"]["content"]["application/json"]["target_metric"];

type SelectChoice = { value: ValidMetricValues; text: string }[];
const metricChoices = [
  { value: "ndcg", text: "Normalized discounted cumulative gain (NDCG)" },
  { value: "recall", text: "Recall" },
  { value: "hit", text: "Hit" },
  { value: "map", text: "Mean average precision (MAP)" },
] as SelectChoice;

export default Vue.extend({
  props: {
    value: {
      type: [Object, Number] as PropType<ResultType>,
      required: false,
    },
  },
  data(): Data {
    return {
      how: 1,
      customConfig: {
        cutoff: 20,
        target_metric: "ndcg",
      },
      existingConfigs: [],
      selectedConfigs: [],
      saveName: "",
    };
  },
  methods: {
    prettifyDate(value: string): string {
      return prettifyDate(value);
    },
    async fetchExistingSplitConfigs(): Promise<void> {
      const results = await getWithRefreshToken<ExistingConfigs>(
        AuthModule,
        `${existingConfigsUrl}?${qs.stringify({ unnamed: false })}`
      );
      this.existingConfigs = results;
    },
  },
  async mounted() {
    await this.fetchExistingSplitConfigs();
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
    selectedId(): number | null {
      if (this.selectedConfigs.length === 0) return null;
      return this.selectedConfigs[0].id;
    },
    metricChoice(): SelectChoice {
      return metricChoices;
    },
    result(): ResultType {
      let result: createConfigArg = new Object();
      if (this.how === 1) {
        return result;
      } else if (this.how === 2) {
        return this.selectedId;
      } else {
        Object.assign(result, this.customConfig);
        if (this.saveName) {
          result.name = this.saveName;
        }
        result["cutoff"] = numberInputValueToNumberOrUndefined(result.cutoff);
        return result;
      }
    },
  },

  components: {
    ValidationObserver,
    ValidationProvider,
    EvaluationConfigView,
  },
});
</script>
