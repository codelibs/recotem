<template>
  <div>
    {{ trainingData }}
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import qs from "qs";
import { components, paths } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";

const trainingDataListURL = "/api/training_data/";
type TrainingData = components["schemas"]["TrainingData"];
type Data = {
  trainingData: TrainingData[];
};

export default Vue.extend({
  data(): Data {
    return {
      trainingData: [],
    };
  },
  computed: {
    projectId(): number | null {
      try {
        return parseInt(this.$route.params.projectId);
      } catch {
        return null;
      }
    },
  },
  methods: {
    async fetchData() {
      if (this.projectId === null) {
        return;
      }
      let trainingData = await getWithRefreshToken<TrainingData[]>(
        AuthModule,
        `trainingDataListURL?${qs.stringify({ project: this.projectId })}`
      );
      if (trainingData !== null) {
        this.trainingData = trainingData;
      }
    },
  },
  async mounted() {
    alert("mounted");
    await this.fetchData();
  },
});
</script>
