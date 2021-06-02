<template>
  <div>{{ trainingData }}</div>
</template>
<script lang="ts">
import Vue from "vue";
import { components, paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";
type TrainingDataType = components["schemas"]["TrainingData"];
const retrieveURL = "/api/training_data";

type Data = {
  trainingData: TrainingDataType | null;
};
export default Vue.extend({
  data(): Data {
    return {
      trainingData: null,
    };
  },
  async mounted() {
    await this.fetchTrainingDataDetail();
  },
  watch: {
    async projectId() {
      await this.fetchTrainingDataDetail();
    },
  },
  methods: {
    async fetchTrainingDataDetail(): Promise<void> {
      if (this.dataId === null) return;
      let result = await getWithRefreshToken<TrainingDataType>(
        AuthModule,
        `${retrieveURL}/${this.dataId}`
      );
      if (result === null) return;
      this.trainingData = result;
    },
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
