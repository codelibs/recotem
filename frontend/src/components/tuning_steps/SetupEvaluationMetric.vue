<template>
  <v-container>
    <ValidationObserver v-slot="{ valid }">
      <div class="d-flex">
        <div>
          <v-radio-group class="mr-6" v-model="how">
            <v-radio :value="1" label="Use Default Values"> </v-radio>
            <v-radio :value="2" label="Use Preset Config"> </v-radio>
            <v-radio :value="3" label="Manually Define"> </v-radio>
          </v-radio-group>
        </div>
        <v-divider vertical v-if="how !== 1"></v-divider>

        <div class="flex-grow-1">
          <v-container v-if="how == 2" fluid>
            <v-list>
              <v-list-item>
                <v-list-item-content>
                  <v-list-item-title>
                    Select preset configurations
                  </v-list-item-title>
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
          </v-container>
          <v-container v-if="how == 3" class="pl-8">
            <ValidationProvider
              name="test_user_ratio"
              rules="max_value:1.0|min_value:0.0"
              v-slot="{ errors }"
            >
              <v-text-field
                label="Ratio of test users."
                type="number"
                v-model.number="customConfig.test_user_ratio"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="n_test_users"
              rules="min_value:0|is_integral"
              v-slot="{ errors }"
            >
              <v-text-field
                label="(Optional) Number of test users."
                type="number"
                v-model.number="customConfig.n_test_users"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="test_user_ratio"
              rules="max_value:1.0|min_value:0.0"
              v-slot="{ errors }"
            >
              <v-text-field
                label="Ratio of held-out interactions."
                type="number"
                v-model.number="customConfig.heldout_ratio"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <ValidationProvider
              name="heldout-interactions"
              rules="min_value:0.0"
              v-slot="{ errors }"
            >
              <v-text-field
                label="(Optional) Number of held-out interactions per user."
                type="number"
                v-model.number="customConfig.n_heldout"
                :error-messages="errors"
              ></v-text-field>
            </ValidationProvider>
            <v-text-field
              label="(Optional) Random Seed."
              type="number"
              v-model.number="customConfig.random_seed"
            ></v-text-field>
            <ValidationProvider
              name="savename"
              rules="splitConfigNameExists"
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
import { getWithRefreshToken, numberInputValueToNumberOrNull } from "@/utils";
import { paths } from "@/api/schema";
import { max_value, min_value } from "vee-validate/dist/rules";
import qs from "qs";

type ExistingConfigs =
  paths["/api/split_config/"]["get"]["responses"]["200"]["content"]["application/json"];
const existingConfigsUrl = "/api/split_config/";

type createConfigArg = Omit<
  paths["/api/split_config/"]["post"]["requestBody"]["content"]["application/json"],
  "id"
>;

type Data = {
  how: number;
  customConfig: createConfigArg;
  id: number | null;
  saveName: string;
  existingConfigs: ExistingConfigs;
  formValid: boolean;
};
export type ResultType = createConfigArg | number | null;

extend("max_value", {
  ...max_value,
  message: "The value must not be greater than 1.0",
});
extend("min_value", {
  ...min_value,
  message: "The value must not be smaller than 0.",
});
extend("is_integral", {
  ...min_value,
  validate(value) {
    if (value === undefined || value === null) return true;
    if (parseInt(value) - value === 0.0) {
      return true;
    } else return false;
  },
  message: "The value must be an integer.",
});

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
  },
});
</script>
