<template>
  <v-card>
    <v-container fluid>
      <div class="d-flex pb-4">
        <div>
          <div class="text-h5">Tuning Jobs</div>
        </div>
        <div class="flex-grow-1"></div>
        <div>
          <v-btn color="info" dark :to="{ name: 'start-tuning-with-data' }">
            <v-icon> mdi-tune </v-icon>
            <span class="pl-2"> Start New Job </span>
          </v-btn>
        </div>
      </div>
      <v-divider> </v-divider>
      <div>
        <tuning-job-list
          :externalCondition="{ data: dataId }"
        ></tuning-job-list>
      </div>
    </v-container>
  </v-card>
</template>
<script lang="ts">
import Vue from "vue";
import TuningJobList from "@/components/TuningJobList.vue";

export default Vue.extend({
  methods: {},
  components: {
    TuningJobList,
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
