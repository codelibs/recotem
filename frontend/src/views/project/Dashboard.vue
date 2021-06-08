<template>
  <div>
    <div class="text-h6 text-center pa-8">
      [Todo] Dashboard view to be drawn
    </div>
  </div>
</template>
<script lang="ts">
import Vue from "vue";
import { components } from "@/api/schema";

const ProjectURL = "/api/project/";
type Project = components["schemas"]["Project"];

interface Data {
  tab: number;
  projects: Project[];
  deleteTargetProject: Project | null;
}

export default Vue.extend({
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
