<template>
  <div>
    <div class="d-flex mb-2">
      <div class="pl-4 text-h6">Trained Models</div>
      <div class="flex-grow-1"></div>
    </div>
    <v-divider></v-divider>
    <div class="pl-4 pr-4 pt-0 mt-0">
      <ModelList
        v-if="projectId !== null"
        :externalCondition="{ project: projectId }"
      />
    </div>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import ModelList from "@/components/ModelList.vue";
export default Vue.extend({
  components: { ModelList },
  computed: {
    projectId(): number | null {
      try {
        return parseInt(this.$route.params.projectId);
      } catch {
        return null;
      }
    },
  },
});
</script>
