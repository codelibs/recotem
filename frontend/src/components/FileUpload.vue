<template>
  <v-container>
    <ValidationObserver v-slot="{ invalid }">
      <v-form>
        <ValidationProvider rules="uploadFileRequired">
          <v-file-input
            :label="fileLabel"
            accept=".csv,.tsv,.ndjson,.jsonl,.pkl,.pickle,.csv.gz,.tsv.gz,.ndjson.gz,.jsonl.gz,.pkl.gz,.pickle.gz"
            v-model="uploadFile"
          >
          </v-file-input>
        </ValidationProvider>
        <div></div>
      </v-form>
      <v-row justify="center" class="mb-4">
        <v-btn
          upload-button
          color="primary"
          :disabled="invalid"
          @click="upload"
          v-if="uploadProgress === null"
          >Upload</v-btn
        >
        <v-col cols="12" v-else>
          <div class="text-subtitle-1 text-center">
            Uploading.. {{ uploadProgress }}%
          </div>
          <v-progress-linear
            :value="uploadProgress"
            :active="uploadProgress !== null"
            :query="true"
          ></v-progress-linear>
        </v-col>
      </v-row>
    </ValidationObserver>
  </v-container>
</template>

<script lang="ts">
import Vue, { PropType } from "vue";
import { AuthModule } from "@/store/auth";
import { AxiosError, AxiosRequestConfig } from "axios";
import { refreshToken, postWithRefreshToken } from "@/utils/request";

import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import { required } from "vee-validate/dist/rules";
extend("uploadFileRequired", {
  ...required,
  message: "Upload file required",
});

type Data = {
  uploadProgress: null | number;
  uploadFile: null | File;
};
interface RecordCreationResponse {
  id: number;
}

export default Vue.extend({
  props: {
    value: {
      type: Number as PropType<number | null>,
      default: null,
    },
    postURL: {
      type: String as PropType<string>,
      required: true,
    },
    fileLabel: {
      type: String as PropType<string>,
      required: true,
    },
  },
  data(): Data {
    return {
      uploadProgress: null,
      uploadFile: null,
    };
  },
  watch: {
    uploadFile() {
      this.$emit("input", null);
    },
  },
  methods: {
    async upload(): Promise<void> {
      await refreshToken(AuthModule);
      if (this.uploadFile === null) return;
      if (AuthModule.currentProjectId === null)
        throw new Error("project id must be set");
      const data = new FormData();
      data.append("project", `${AuthModule.currentProjectId}`);
      data.append("file", this.uploadFile);

      this.uploadProgress = 0;

      const config: AxiosRequestConfig = {
        onUploadProgress: (progressEvent: any) => {
          var percentCompleted = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total
          );
          this.uploadProgress = percentCompleted;
        },
      };
      const result = await postWithRefreshToken<
        FormData,
        RecordCreationResponse
      >(AuthModule, this.postURL, data, config).catch((error: AxiosError) => {
        if (error.response === undefined) {
          throw error;
        }
        if (error.response.status !== 400) {
          throw error;
        }
        this.uploadProgress = null;
        this.uploadFile = null;
        return null;
      });
      if (result !== null) {
        this.uploadProgress = null;
        this.$emit("input", result.id);
      } else {
        this.$emit("input", null);
      }
    },
  },
  components: {
    ValidationObserver,
    ValidationProvider,
  },
});
</script>
