<style scoped></style>
<template>
  <v-container>
    <div class="d-flex">
      <div>
        <v-radio-group class="mr-12" v-model="how">
          <v-radio :value="1" label="Use Default Values"> </v-radio>
          <v-radio
            v-if="existingConfigs.length > 0"
            :value="2"
            label="Use Preset Config"
          >
          </v-radio>
          <v-radio :value="3" label="Manually Define"> </v-radio>
        </v-radio-group>
      </div>
      <v-divider vertical v-if="how !== 1"></v-divider>

      <div class="flex-grow-1">
        <v-container v-if="how == 3">
          <v-form>
            <div class="d-flex align-center">
              <v-checkbox
                v-model="saveConfig"
                hide-details
                class="shrink mr-2 mt-0"
              ></v-checkbox>
              <v-text-field
                :disabled="!saveConfig"
                label="Save this config with name"
                v-model="customConfig.name"
              >
              </v-text-field>
            </div>
            <v-text-field
              label="Ratio of test users."
              type="number"
              v-model.number="customConfig.test_user_ratio"
            ></v-text-field>
            <v-text-field
              label="(Optional) Maximal Number of test interactions per user."
              type="number"
              v-model.number="customConfig.n_test_users"
            ></v-text-field>

            <v-text-field
              label="Ratio of held-out interactions."
              type="number"
              v-model.number="customConfig.heldout_ratio"
            ></v-text-field>
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
          </v-form>
        </v-container>
      </div>
    </div>
  </v-container>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";
import { components, paths } from "@/api/schema";

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
  saveConfig: boolean;
  existingConfigs: ExistingConfigs;
};
export type ResultType = createConfigArg | number | null;

export default Vue.extend({
  props: {
    value: {
      type: Object as PropType<ResultType>,
      required: false,
    },
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
        return this.customConfig;
      }
    },
  },
  data(): Data {
    return {
      how: 1,
      id: null,
      customConfig: {
        name: null,
        scheme: "RG",
        heldout_ratio: 0.1,
        n_heldout: null,
        test_user_ratio: 1.0,
        n_test_users: null,
        random_seed: undefined,
      },
      saveConfig: false,
      existingConfigs: [],
    };
  },
});
</script>
