<style scoped>
.refresh-divider {
  border-bottom: 1px solid blanchedalmond;
  margin-top: 6px;
}
.log-text-background {
  background-color: #424242;
  color: blanchedalmond;
  padding: 20px;
  padding-bottom: 5px;
  overflow-y: scroll;
  height: 300px;
  overflow-x: hidden;
}
.single-record {
  display: flex;
  padding: 2px;
}
.log-body {
  font-size: 0.8rem;
  flex-grow: 1;
}
.time-info {
  width: 200px;
  white-space: pre-wrap;
  font-size: 0.7rem;
}
</style>
<template>
  <v-card class="log-text-background">
    <v-row v-for="(log, i) in logs" :key="i">
      <v-col cols="3" class="time-info">
        {{ prettifyDate(log.ins_datetime) }}</v-col
      >
      <v-col cols="8" class="log-body"> {{ log.contents }} </v-col>
    </v-row>
  </v-card>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import { paths } from "@/api/schema";
import { getWithRefreshToken } from "@/utils";
import { prettifyDate } from "@/utils/date";
import { AuthModule } from "@/store/auth";
import qs from "qs";

const logListURL = "/api/task_log/";
type LogResultType =
  paths["/api/task_log/"]["get"]["responses"]["200"]["content"]["application/json"];

type Data = {
  logs: LogResultType;
  pollingStop: boolean;
  shownLogIdMax: number;
  shownLogIdMin: number;
  minId: number;
};

export default Vue.extend({
  props: {
    condition: {
      type: Object as PropType<Record<string, string>>,
      default: Object,
    },
    complete: {
      type: Boolean as PropType<boolean>,
      required: true,
    },
  },
  data(): Data {
    return {
      logs: [],
      pollingStop: false,
      shownLogIdMax: parseFloat("infinity"),
      shownLogIdMin: 0,
      minId: parseFloat("infinity"),
    };
  },
  beforeDestroy() {
    this.pollingStop = true;
  },
  methods: {
    prettifyDate(x: string): string {
      return prettifyDate(x);
    },
    async polling(): Promise<void> {
      for (;;) {
        await new Promise((resolve: any) => setTimeout(resolve, 5000));
        await this.fetchData();
        if (this.complete) {
          break;
        }
        if (this.pollingStop) {
          break;
        }
      }
    },
    async fetchData(): Promise<void> {
      let queryString = qs.stringify({
        id_gt: this.maxID,
        ...this.condition,
      });
      const result = await getWithRefreshToken<LogResultType>(
        AuthModule,
        `${logListURL}?${queryString}`
      );
      if (result === null) {
        return;
      }
      result.sort((a, b) => a.id - b.id);
      this.logs.splice(this.logs.length, 0, ...result);
    },
  },
  computed: {
    maxID(): number {
      if (this.logs.length === 0) return 0;
      return this.logs[this.logs.length - 1].id;
    },
    shownLogs(): LogResultType {
      return this.logs.filter(
        (v) => v.id >= this.shownLogIdMin && v.id <= this.shownLogIdMax
      );
    },
  },
  watch: {},
  async mounted() {
    await this.fetchData();
    await this.polling();
  },
});
</script>
