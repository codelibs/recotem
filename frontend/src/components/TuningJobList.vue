<template>
  <v-list>
    <v-list-item v-for="(td, i) in parameterTuningJobList" :key="i">
      <v-list-item-title>
        {{ td.name }} <br />
        id: {{ td.id }}
      </v-list-item-title>
      {{ td.ins_datetime }}
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
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { components, paths } from "@/api/schema";

type TrainingDataType = components["schemas"]["TrainingDataDetail"];
type ParameterTuningJobSetType = TrainingDataType["parametertuningjob_set"];

export default Vue.extend({
  props: {
    parameterTuningJobList: {
      type: Array as PropType<ParameterTuningJobSetType>,
      required: true,
    },
  },
  async mounted() {
    console.log(this.parameterTuningJobList);
  },
});
</script>
