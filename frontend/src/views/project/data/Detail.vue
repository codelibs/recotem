<template>
  <div>
    <v-list v-if="trainingData !== null">
      <v-list-item
        v-for="(td, i) in trainingData.parametertuningjob_set"
        :key="i"
      >
        <v-list-item-title>
          {{ td.name }} <br />
          id: {{ td.id }}
        </v-list-item-title>
        <v-list-item-subtitle>
          {{ td.ins_datetime }}
        </v-list-item-subtitle>
        <ul>
          <li
            v-for="(tpjl, i_tpjl) in td.taskandparameterjoblink_set"
            :key="i_tpjl"
          >
            {{ tpjl.task.status }}
          </li>
        </ul>
      </v-list-item>
    </v-list>
  </div>
</template>
<script lang="ts">
import Vue from "vue";
import { components, paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken } from "@/utils";
type TrainingDataType = components["schemas"]["TrainingDataDetail"];
const retrieveURL = "/api/data_detail";

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
