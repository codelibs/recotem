<style scoped>
table th {
  padding-right: 40px;
  text-align: left;
}
table tr {
  padding: 5px;
  height: 32px;
}
</style>
<template>
  <div>
    <table>
      <tbody>
        <tr v-if="evaluationConfigDetail.name">
          <th>name</th>
          <td>"{{ evaluationConfigDetail.name }}"</td>
        </tr>
        <tr>
          <th>Optimization target metric</th>
          <td>
            {{ evaluationConfigDetail.target_metric }}
          </td>
        </tr>
        <tr>
          <th>Cutoff</th>
          <td>
            {{ evaluationConfigDetail.cutoff }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { components } from "@/api/schema.ts";

type EvaluationConfig = components["schemas"]["EvaluationConfig"];
export default Vue.extend({
  props: {
    evaluationConfigDetail: {
      type: Object as PropType<EvaluationConfig>,
      required: true,
    },
  },
});
</script>
