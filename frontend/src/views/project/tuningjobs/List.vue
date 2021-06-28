<template>
  <v-container>
    <v-card>
      <v-card-title>
        <div class="pl-4 text-h6">Tuning Jobs</div>
        <div class="flex-grow-1"></div>
        <div>
          <v-btn :to="{ name: 'start-tuning' }" color="primary">
            <v-icon>mdi-tune</v-icon> Start new job</v-btn
          >
        </div>
      </v-card-title>
      <div class="pl-4 pr-4 pt-0 mt-0">
        <tuning-job-list
          v-if="projectId !== null"
          :externalCondition="{ data__project: projectId }"
        ></tuning-job-list>
      </div>
    </v-card>
  </v-container>
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
