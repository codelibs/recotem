<style scoped>
.error-content {
  background-color: #424242;
  color: blanchedalmond;
  overflow-y: scroll;
  white-space: pre-wrap;
  max-height: 300px;
  min-height: 40px;
  max-width: 500px;
}
</style>
<template>
  <div>
    <router-view> </router-view>
    <v-overlay
      :value="(errorWithResponse || errorWithoutResponse) !== null"
      :z-index="999"
    >
      <v-card>
        <template v-if="errorWithResponse !== null">
          <v-card-title>
            <v-alert>
              Error with status code {{ errorWithResponse.status }}:
            </v-alert>
          </v-card-title>
          <v-card-text>
            <div
              v-if="errorWithResponse.url !== undefined"
              class="text-caption"
            >
              Requst to {{ errorWithResponse.url }} failed with the message:
            </div>
            <div class="text-caption error-content">
              {{ errorWithResponse.data }}
            </div>
          </v-card-text>
        </template>
        <template v-if="errorWithoutResponse !== null">
          <v-card-title>
            <v-alert> Error: {{ errorWithoutResponse.name }} </v-alert>
          </v-card-title>
          <v-card-text>
            <div class="text-caption error-content">
              {{ errorWithoutResponse.message }}
            </div>
          </v-card-text>
        </template>

        <v-card-actions>
          <v-btn ignore-error color="error" @click="ignoreError">Ignore</v-btn>
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
    errorWithoutResponse(): {
      message: string;
      name: string;
    } | null {
      if (ExceptionModule.axiosError === null) {
        return null;
      }
      if (ExceptionModule.axiosError.response !== undefined) {
        return null;
      }
      const message = ExceptionModule.axiosError.message;
      const name = ExceptionModule.axiosError.name;

      return { message, name };
    },
  },
});
</script>
