<style scoped></style>
<template>
  <v-container>
    <ValidationObserver v-slot="{ valid }">
      <div class="d-flex">
        <div>
          <v-radio-group class="mr-12" v-model="how">
            <v-radio :value="1" label="Use Default Values"> </v-radio>
            <v-radio :value="2" label="Use Preset Config"> </v-radio>
            <v-radio :value="3" label="Manually Define"> </v-radio>
          </v-radio-group>
        </div>
        <v-divider vertical v-if="how !== 1"></v-divider>

        <div class="flex-grow-1">
          <v-container v-if="how == 2">
            <v-list>
              <v-list-item v-for="i in 5" :key="i" @click="id = i">
                id: {{ i }}
              </v-list-item>
            </v-list>
          </v-container>
          <v-container v-if="how == 3">
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
              name="test_user_ratio"
              rules="min_value:0"
              v-slot="{ errors }"
            >
              <v-text-field
                label="(Optional) Maximal Number of test interactions per user."
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
            <v-text-field
              label="(Optional) Number of test users."
              type="number"
              v-model.number="customConfig.n_test_users"
            ></v-text-field>
            <v-text-field
              label="(Optional) Random Seed."
              type="number"
              v-model.number="customConfig.random_seed"
            ></v-text-field>
            <v-text-field label="Save this config with name" v-model="saveName">
            </v-text-field>
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
import { paths } from "@/api/schema";
import { max_value, min_value } from "vee-validate/dist/rules";

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
  message: "value must be smaller than 1.0",
});
extend("min_value", min_value);

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
      if (this.how === 1) {
        return {} as createConfigArg;
      } else if (this.how === 2) {
        return this.id;
      } else {
        if (this.saveName) {
          return { ...this.customConfig, name: this.saveName };
        } else {
          return this.customConfig;
        }
      }
    },
  },

  components: {
    ValidationObserver,
    ValidationProvider,
  },
});
</script>
