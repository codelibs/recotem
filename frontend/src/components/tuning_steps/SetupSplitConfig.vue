<style scoped></style>
<template>
  <v-container>
    <ValidationObserver v-slot="{ valid }">
      <div class="d-flex">
        <div>
          <v-radio-group class="mr-6" v-model="how">
            <v-radio name="use-default" :value="1" label="Use Default Values">
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
          <v-container v-if="how == 2" fluid class="pt-0">
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
                  <SplitConfigView :splitConfigDetail="item" />
                </td>
              </template>
            </v-data-table>
          </v-container>
          <v-container v-if="how == 3" class="pl-8">
            <ValidationProvider
              name="test_user_ratio"
              rules="validRatio"
              v-slot="{ errors }"
            >
              <v-text-field
                name="test_user_ratio"
                label="Ratio of test users."
                type="number"
                v-model.number="customConfig.test_user_ratio"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="n_test_users"
              rules="isPositiveInteger"
              v-slot="{ errors }"
            >
              <v-text-field
                name="n_test_users"
                label="Number of test users."
                type="number"
                v-model.number="customConfig.n_test_users"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="test_heldout_ratio"
              rules="validRatio"
              v-slot="{ errors }"
            >
              <v-text-field
                name="test_heldout_ratio"
                label="Ratio of held-out interactions."
                type="number"
                v-model.number="customConfig.heldout_ratio"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="heldout_interactions"
              rules="isPositiveInteger"
              v-slot="{ errors }"
            >
              <v-text-field
                name="heldout_interactions"
                label="Number of held-out interactions per user."
                type="number"
                v-model.number="customConfig.n_heldout"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="random_seed"
              rules="isNonnegativeInteger"
              v-slot="{ errors }"
            >
              <v-text-field
                name="random_seed"
                label="Random Seed."
                type="number"
                v-model.number="customConfig.random_seed"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="savename"
              rules="splitConfigNameExists"
              v-slot="{ errors }"
              :debounce="300"
            >
              <v-text-field
                label="Save this config with name"
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
import {
  isInteger,
  isPositiveInteger,
  isNonnegativeInteger,
  validRatio,
} from "@/utils/rules";
import { numberInputValueToNumberOrNull } from "@/utils/conversion";
import { prettifyDate } from "@/utils/date";
import { paths } from "@/api/schema";
import SplitConfigView from "@/components/SplitConfigView.vue";
import qs from "qs";

type ExistingConfigs =
  paths["/api/split_config/"]["get"]["responses"]["200"]["content"]["application/json"];
const existingConfigsUrl = "/api/split_config/";

type createConfigArg = Omit<
  paths["/api/split_config/"]["post"]["requestBody"]["content"]["application/json"],
  "id" | "ins_datetime"
>;

export type ResultType = createConfigArg | number | null;

extend("isPositiveInteger", isPositiveInteger);
extend("isInteger", isInteger);
extend("validRatio", validRatio);
extend("isNonnegativeInteger", isNonnegativeInteger);

extend("splitConfigNameExists", {
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
type Data = {
  how: number;
  customConfig: createConfigArg;
  saveName: string;
  existingConfigs: ExistingConfigs;
  selectedConfigs: ExistingConfigs;
  formValid: boolean;
};
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
      selectedConfigs: [],
      customConfig: {
        scheme: "RG",
        heldout_ratio: 0.1,
        n_heldout: null,
        test_user_ratio: 1.0,
        n_test_users: null,
        random_seed: undefined,
      },
      formValid: true,
      existingConfigs: [],
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
        result["heldout_ratio"] = numberInputValueToNumberOrNull(
          result.heldout_ratio
        );

        result["n_heldout"] = numberInputValueToNumberOrNull(result.n_heldout);
        result["test_user_ratio"] = numberInputValueToNumberOrNull(
          result.test_user_ratio
        );
        result["n_test_users"] = numberInputValueToNumberOrNull(
          result.n_test_users
        );
        result["random_seed"] = numberInputValueToNumberOrNull(
          result.random_seed
        );
        return result;
      }
    },
  },

  components: {
    ValidationObserver,
    ValidationProvider,
    SplitConfigView,
  },
});
</script>
