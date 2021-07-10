<style scoped>
.error-content {
  background-color: #424242;
  color: blanchedalmond;
  overflow-y: scroll;
  max-height: 500px;
}
</style>
<template>
  <div>
    <router-view> </router-view>
    <v-overlay
      :value="errorWithResponse !== null"
      v-if="errorWithResponse !== null"
      :z-index="999"
    >
      <v-card>
        <v-card-title>
          <v-alert>
            Error with status code {{ errorWithResponse.status }}:
          </v-alert>
        </v-card-title>
        <v-card-text>
          <div v-if="errorWithResponse.url !== undefined" class="text-caption">
            Requst to {{ errorWithResponse.url }} failed with the message:
          </div>
          <div class="text-caption error-content">
            {{ errorWithResponse.data }}
          </div>
        </v-card-text>
        <v-card-actions>
          <v-btn color="error" @click="ignoreError">Ignore</v-btn>
        </v-card-actions>
      </v-card>
    </v-overlay>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { guard } from "@/router/guard.ts";
import { ExceptionModule } from "@/store/exception";

export default Vue.extend({
  beforeRouteEnter: guard,
  beforeRouteUpdate: guard,
  methods: {
    ignoreError() {
      ExceptionModule.resetAxiosError();
    },
  },
  computed: {
    errorWithResponse(): {
      status: number;
      url: string | undefined;
      data: unknown;
    } | null {
      if (ExceptionModule.axiosError === null) {
        return null;
      }
      if (ExceptionModule.axiosError.response === undefined) {
        return null;
      }
      return ((resp) => {
        return { status: resp.status, data: resp.data, url: resp.config.url };
      })(ExceptionModule.axiosError.response);
    },
  },
});
</script>
