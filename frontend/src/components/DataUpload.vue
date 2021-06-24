<template>
  <v-dialog v-model="uploadDialog" max-width="800">
    <template v-slot:activator="{ on, attrs }">
      <v-btn class="mr-4" :color="color" dark v-on="on" v-bind="attrs">
        <v-icon> mdi-upload</v-icon> Upload
      </v-btn>
    </template>
    <v-card>
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

          <v-alert
            v-for="(message, i_m) in uploadErrorMessages"
            type="error"
            :key="i_m"
          >
            {{ message }}
          </v-alert>
        </ValidationObserver>
      </v-container>
    </v-card>
  </v-dialog>
</template>
<script lang="ts">
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
  uploadErrorMessages: string[];
  uploadDialog: boolean;
};

import Vue, { PropType } from "vue";
export default Vue.extend({
  props: {
    value: {
      type: Boolean as PropType<boolean>,
      default: false,
    },

    projectId: {
      type: Number as PropType<number>,
      required: true,
    },
    postURL: {
      type: String as PropType<string>,
      required: true,
    },
    fileLabel: {
      type: String as PropType<string>,
      required: true,
    },
    color: {
      type: String as PropType<string>,
      default: "green",
    },
  },
  data(): Data {
    return {
      uploadProgress: null,
      uploadFile: null,
      uploadErrorMessages: [],
      uploadDialog: false,
    };
  },
  watch: {
    uploadDialog(nv: boolean) {
      this.$emit("input", nv);
    },
  },
  methods: {
    async upload(): Promise<void> {
      await refreshToken(AuthModule);
      if (this.uploadFile === null) return;
      if (this.projectId === null) return;
      const data = new FormData();
      data.append("project", `${this.projectId}`);
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
      const result = await postWithRefreshToken<FormData, unknown>(
        AuthModule,
        this.postURL,
        data,
        config
      ).catch((error: AxiosError) => {
        console.log(error);
        const errorDetail: undefined | string | string[] = error.response?.data;
        if (errorDetail !== undefined) {
          if (typeof errorDetail === "string") {
            this.uploadErrorMessages = [errorDetail];
          } else {
            this.uploadErrorMessages = errorDetail;
          }
        }
        console.log(this.uploadErrorMessages);
        this.uploadProgress = null;
        return undefined;
      });
      if (result) {
        this.uploadDialog = false;
        this.uploadProgress = null;
      }
    },
  },
  components: {
    ValidationObserver,
    ValidationProvider,
  },
});
</script>
