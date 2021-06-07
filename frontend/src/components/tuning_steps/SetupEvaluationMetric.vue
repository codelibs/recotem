<template>
  <v-container>
    <ValidationObserver v-slot="{ valid }">
      <div class="d-flex">
        <div>
          <v-radio-group class="mr-6" v-model="how">
            <v-radio :value="1" label="Use Default Values"> </v-radio>
            <v-radio
              :disabled="existingConfigs.length == 0"
              :value="2"
              label="Use Preset Config"
            >
            </v-radio>
            <v-radio :value="3" label="Manually Define"> </v-radio>
          </v-radio-group>
        </div>
        <v-divider vertical v-if="how !== 1"></v-divider>

        <div class="flex-grow-1">
          <v-container v-if="how == 2" fluid class="pt-0 mt-0">
            <v-list v-if="existingConfigs.length > 0">
              <v-list-item>
                <v-list-item-content>
                  <v-list-item-subtitle>
                    Select preset configurations
                  </v-list-item-subtitle>
                </v-list-item-content>
              </v-list-item>
              <v-divider></v-divider>

              <template v-for="(config, i) in existingConfigs">
                <v-list-item
                  :key="i"
                  @click="handlePresetConfigClick(config.id)"
                  :input-value="id === config.id"
                >
                  <v-list-item-content>
                    <v-list-item-title>
                      {{ config.name }}
                    </v-list-item-title>
                    <v-list-item-subtitle>
                      id: {{ config.id }}
                    </v-list-item-subtitle>
                  </v-list-item-content>
                  id: {{ config.id }}
                </v-list-item>
                <v-divider :key="i + 0.5"></v-divider>
              </template>
            </v-list>
            <div v-else class="text-h5 text-center pa-4">
              No available preset found.
            </div>
          </v-container>
          <v-container v-if="how == 3" class="pl-8">
            <v-select
              :items="metricChoice"
              v-model="customConfig.target_metric"
            >
            </v-select>
            <ValidationProvider
              name="test_user_ratio"
              rules="min_value:0.0|"
              v-slot="{ errors }"
            >
              <v-text-field
                label="Ratio of test users."
                type="number"
                v-model.number="customConfig.cutoff"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="savename"
              rules="evaluationConfigNameExists"
              v-slot="{ errors }"
              :debounce="500"
            >
              <v-text-field
                label="(Optional) Save this config with name"
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
          (how == 3 && valid) || how == 1 || (how == 2 && id !== null)
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
import { is_integral, max_value, min_value } from "@/utils/rules";
import { numberInputValueToNumberOrNull } from "@/utils/conversion";
import { paths } from "@/api/schema";
import qs from "qs";

type ExistingConfigs =
  paths["/api/evaluation_config/"]["get"]["responses"]["200"]["content"]["application/json"];
const existingConfigsUrl = "/api/evaluation_config/";

type createConfigArg = Omit<
  paths["/api/evaluation_config/"]["post"]["requestBody"]["content"]["application/json"],
  "id"
>;

type Data = {
  how: number;
  customConfig: createConfigArg;
  id: number | null;
  saveName: string;
  existingConfigs: ExistingConfigs;
};
export type ResultType = createConfigArg | number | null;

extend("max_value", max_value);
extend("min_value", min_value);
extend("is_integral", is_integral);

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
type SelectChoice = { value: string; text: string }[];
const metricChoices: SelectChoice = [
  { value: "ndcg", text: "Normalized discounted cumulative gain (NDCG)" },
  { value: "recall", text: "Recall" },
  { value: "hit", text: "Hit" },
  { value: "map", text: "Mean average precision (MAP)" },
];

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
      id: null,
      customConfig: {
        cutoff: 20,
        target_metric: "ndcg",
      },
      existingConfigs: [],
      saveName: "",
    };
  },
  methods: {
    handlePresetConfigClick(id: number): void {
      if (this.id === null) {
        this.id = id;
        return;
      }
      // id values already set
      if (this.id === id) {
        // unselect
        this.id = null;
        return;
      }
      this.id = id;
    },
    async fetchExistingSplitConfigs(): Promise<void> {
      const results = await getWithRefreshToken<ExistingConfigs>(
        AuthModule,
        existingConfigsUrl
      );
      if (results !== null) {
        this.existingConfigs = results;
      }
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
    metricChoice(): SelectChoice {
      return metricChoices;
    },
    result(): ResultType {
      let result: createConfigArg = new Object();
      if (this.how === 1) {
        return result;
      } else if (this.how === 2) {
        return this.id;
      } else {
        Object.assign(result, this.customConfig);
        if (this.saveName) {
          result.name = this.saveName;
        }
        result["cutoff"] = numberInputValueToNumberOrNull(result.cutoff);
        return result;
      }
    },
  },

  components: {
    ValidationObserver,
    ValidationProvider,
  },
});
</script>
