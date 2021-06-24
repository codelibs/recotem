<template>
  <div>
    <template
      v-if="
        statusCodeSetToString === 'Starting' ||
        statusCodeSetToString === 'InProgress'
      "
    >
      <v-progress-circular indeterminate :size="10" />
      <span class="text-subtitle"> In Progress</span>
    </template>
    <template v-if="statusCodeSetToString === 'Failure'">
      <v-icon color="red"> mdi-close-circle-outline </v-icon>
      <span class="text-subtitle"> Failed </span>
    </template>
    <template v-if="statusCodeSetToString === 'Complete'">
      <v-icon color="green"> mdi-check</v-icon>
      <span class="text-subtitle"> Complete </span>
    </template>
  </div>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { components } from "@/api/schema.ts";

type TaskType = components["schemas"]["TaskResult"];
type IconTypes =
  | "Starting"
  | "Failure"
  | "InProgress"
  | "Revoked"
  | "Complete"
  | "Unknown";

export default Vue.extend({
  props: {
    tasks: {
      type: Array as PropType<{ task: TaskType }[]>,
      required: true,
    },
    value: {
      type: Boolean as PropType<boolean>,
      default: false,
    },
  },
  watch: {
    complete(nv: boolean): void {
      console.log(nv);
      this.$emit("input", nv);
    },
  },
  async mounted(): Promise<void> {
    let complete = this.complete;
    this.$emit("input", complete);
  },
  computed: {
    complete(): boolean {
      switch (this.statusCodeSetToString) {
        case "Starting":
          return false;
        case "Failure":
          return true;
        case "InProgress":
          return false;
        case "Revoked":
          return true;
        case "Complete":
          return true;
        case "Unknown":
          return true;
        default:
          return true;
      }
    },
    statusCodeSetToString(): IconTypes {
      if (this.tasks.length === 0) return "Starting";
      for (let s of this.tasks) {
        if (s.task.status === "FAILURE") {
          return "Failure";
        }
      }
      for (let s of this.tasks) {
        if (s.task.status === "STARTED") {
          return "InProgress";
        }
      }
      for (let s of this.tasks) {
        if (s.task.status === "REVOKED") {
          return "Revoked";
        }
      }
      let allSuccess = true;
      for (let s of this.tasks) {
        if (s.task.status !== "SUCCESS") {
          allSuccess = false;
        }
      }
      if (allSuccess) {
        return "Complete";
      }
      return "Unknown";
    },
  },
});
</script>
