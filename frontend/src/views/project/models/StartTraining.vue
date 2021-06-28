<template>
  <div>
    <div>Setup training:</div>
    <v-stepper v-model="step">
      <v-stepper-header>
        <v-stepper-step step="1"> Data </v-stepper-step>
        <v-divider></v-divider>
        <v-stepper-step step="2"> Configuration </v-stepper-step>
      </v-stepper-header>
      <v-stepper-content step="1" class="pt-2">
        <TrainingDataList isSelection v-model="dataId" />
        <v-row>
          <v-col cols="6" />
          <v-col cols="6">
            <v-btn :disabled="dataId === null" @click="step = 2" color="info">
              Continue <v-icon> mdi-arrow-right</v-icon>
            </v-btn>
          </v-col>
        </v-row>
      </v-stepper-content>
      <v-stepper-content step="2">
        <ModelConfigList v-model="modelConfigurationId" />
        <div class="d-flex justify-center mt-8">
          <div>
            <v-btn class="mr-2" @click="step = 1">
              <v-icon>mdi-arrow-left</v-icon>Previous
            </v-btn>
            <v-btn
              class="ml-2"
              :disabled="modelConfigurationId === null"
              @click="trainModel"
              color="info"
            >
              <v-icon>mdi-calculator</v-icon>Start Training
            </v-btn>
          </div>
        </div>
      </v-stepper-content>
    </v-stepper>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { AuthModule } from "@/store/auth";
import { postWithRefreshToken } from "@/utils/request";
import { paths } from "@/api/schema";

import TrainingDataList from "@/components/TrainingDataList.vue";
import ModelConfigList from "@/components/ModelConfigList.vue";

const modelCreationURL = "/api/trained_model/";
type PostResult =
  paths["/api/trained_model/"]["post"]["responses"]["201"]["content"]["application/json"];
type PostArg = {
  data_loc: number;
  configuration: number;
};

type Data = {
  step: number;
  dataId: number | null;
  modelConfigurationId: number | null;
};
export default Vue.extend({
  data(): Data {
    return {
      step: 1,
      dataId: null,
      modelConfigurationId: null,
    };
  },
  methods: {
    async trainModel(): Promise<void> {
      if (this.dataId === null || this.modelConfigurationId === null) {
        throw "Invalid state.";
      }
      const result = await postWithRefreshToken<PostArg, PostResult>(
        AuthModule,
        modelCreationURL,
        {
          data_loc: this.dataId,
          configuration: this.modelConfigurationId,
        }
      );
      if (result !== null) {
        this.$router.push({
          name: "trained-model-detail",
          params: { trainedModelId: `${result.id}` },
        });
      }
    },
  },
  components: {
    TrainingDataList,
    ModelConfigList,
  },
  computed: {},
});
</script>
