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
        <tr v-if="splitConfigDetail.name">
          <th>name</th>
          <td>"{{ splitConfigDetail.name }}"</td>
        </tr>
        <tr v-if="splitConfigDetail.n_heldout !== null">
          <th>held-out interactions per user</th>
          <td>
            {{ splitConfigDetail.n_heldout }}
          </td>
        </tr>
        <tr v-else>
          <th>held-out ratio</th>
          <td>
            {{ splitConfigDetail.heldout_ratio }}
          </td>
        </tr>
        <tr v-if="splitConfigDetail.n_test_users !== null">
          <th>maximal users participating in the test</th>
          <td>{{ splitConfigDetail.n_test_users }}</td>
        </tr>
        <tr>
          <th>held-out users ratio</th>
          <td>
            {{ splitConfigDetail.test_user_ratio }}
          </td>
        </tr>
        <tr>
          <th>Random seed</th>
          <td>
            {{ splitConfigDetail.random_seed }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { components } from "@/api/schema.ts";

type SplitConfig = components["schemas"]["SplitConfig"];
export default Vue.extend({
  props: {
    splitConfigDetail: {
      type: Object as PropType<SplitConfig>,
      required: true,
    },
  },
});
</script>
