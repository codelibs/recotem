<template>
  <tuning-job-list
    v-if="projectId !== null"
    :externalCondition="{ project: projectId }"
  ></tuning-job-list>
</template>
<script lang="ts">
import Vue from "vue";
import TuningJobList from "@/components/TuningJobList.vue";
export default Vue.extend({
  components: { TuningJobList },
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
