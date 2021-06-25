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
  <div v-if="modelConfigDetail !== null">
    <table>
      <tbody>
        <tr>
          <th>Recommender type</th>
          <td>
            {{ modelConfigDetail.recommender_class_name }}
          </td>
        </tr>
        <template v-if="parameterKeys !== null">
          <tr v-for="(paramName, i) in parameterKeys" :key="i">
            <th>{{ paramName }}</th>
            <td v-if="parameters !== null">{{ parameters[paramName] }}</td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { paths } from "@/api/schema.ts";
import { getWithRefreshToken } from "@/utils/request";
import { AuthModule } from "@/store/auth";
type ModelConfig =
  paths["/api/model_configuration/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];
const URL = "/api/model_configuration/";

type Data = {
  modelConfigDetail: ModelConfig | null;
};
export default Vue.extend({
  props: {
    modelConfigId: {
      type: Number as PropType<number>,
      required: true,
    },
  },
  data(): Data {
    return {
      modelConfigDetail: null,
    };
  },
  async mounted(): Promise<void> {
    this.modelConfigDetail = await getWithRefreshToken<ModelConfig>(
      AuthModule,
      `${URL}/${this.modelConfigId}/`
    );
  },
  computed: {
    parameters(): Record<string, string | number | null> | null {
      if (this.modelConfigDetail === null) return null;
      return JSON.parse(this.modelConfigDetail.parameters_json);
    },
    parameterKeys(): string[] | null {
      if (this.parameters === null) return null;
      const result = Object.keys(this.parameters);
      return result;
    },
  },
});
</script>
