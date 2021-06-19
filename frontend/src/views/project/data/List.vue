<template>
  <div class="mt-1">
    <div class="d-flex align-center">
      <div class="text-h6 pl-4">Uploaded files</div>
      <div class="flex-grow-1"></div>
      <v-dialog v-model="uploadDialog" max-width="800">
        <template v-slot:activator="{ on, attrs }">
          <v-btn
            class="mr-4"
            color="green"
            dark
            v-on="on"
            v-bind="attrs"
            :disabled="uploadProgress !== null"
          >
            <v-icon> mdi-upload</v-icon> Upload new data
          </v-btn>
        </template>
        <v-card>
          <v-container>
            <ValidationObserver v-slot="{ invalid }">
              <v-form>
                <ValidationProvider rules="uploadFileRequired">
                  <v-file-input
                    label="The interaction data."
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
    </div>

    <v-container v-if="trainingData !== undefined && trainingData.length > 0">
      <v-list>
        <v-divider></v-divider>
        <template v-for="(td, i) in trainingData">
          <v-list-item
            :key="i"
            :to="{ name: 'data-detail', params: { dataId: td.id } }"
          >
            <v-list-item-content>
              <v-list-item-title>
                {{ td.basename }}
              </v-list-item-title>
              <v-list-item-subtitle>
                <v-row>
                  <v-col cols="5"> {{ td.ins_datetime }} </v-col>
                  <v-col cols="4" class="ml-4">
                    {{ prettyFileSize(td.filesize) }}
                  </v-col>
                  <v-spacer></v-spacer>
                </v-row>
              </v-list-item-subtitle>
            </v-list-item-content>
            <v-list-item-action>
              <v-btn
                icon
                ripple=""
                :to="{
                  name: 'start-tuning-with-data',
                  params: { dataId: td.id },
                }"
              >
                <v-icon color="primary">mdi-tune</v-icon>
              </v-btn>
            </v-list-item-action>
          </v-list-item>
          <v-divider :key="i + 0.5"></v-divider>
        </template>
      </v-list>
      <v-pagination v-model="pageNumber" :length="maxPageSize"> </v-pagination>
    </v-container>
    <div v-else class="text-center pt-12 text-h6">No data yet.</div>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import qs from "qs";
import { components, paths } from "@/api/schema";
import { getWithRefreshToken, postWithRefreshToken } from "@/utils";
import { refreshToken } from "@/utils/request";
import { prettyFileSize } from "@/utils/conversion";
import { computeMaxPage } from "@/utils/pagination";

import { AuthModule } from "@/store/auth";
import { AxiosError, AxiosRequestConfig } from "axios";
import { required } from "vee-validate/dist/rules";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";

extend("uploadFileRequired", {
  ...required,
  message: "Upload file required",
});

const trainingDataListURL = "/api/training_data/";
type TrainingData = components["schemas"]["TrainingData"];
type responseType =
  paths["/api/training_data/"]["get"]["responses"]["200"]["content"]["application/json"];
type responseContent = responseType["results"];

const pageSize = 10;

type Data = {
  trainingData: responseContent;
  pageNumber: number;
  totalSize: number | null;
  maxPageNumber: number | null;
  uploadDialog: boolean;
  uploadProgress: null | number;
  uploadFile: null | File;
  uploadErrorMessages: string[];
  deleteDialog: boolean;
  deleteTargetId: null | TrainingData;
};

export default Vue.extend({
  data(): Data {
    return {
      trainingData: undefined,
      pageNumber: 1,
      totalSize: null,
      maxPageNumber: null,
      uploadDialog: false,
      uploadProgress: null,
      uploadFile: null,
      uploadErrorMessages: [],
      deleteDialog: false,
      deleteTargetId: null,
    };
  },
  computed: {
    maxPageSize(): number | null {
      return computeMaxPage(this.totalSize, pageSize);
    },
    projectId(): number | null {
      try {
        return parseInt(this.$route.params.projectId);
      } catch {
        return null;
      }
    },
  },
  watch: {
    async pageNumber() {
      await this.fetchData();
    },
  },
  methods: {
    prettyFileSize(value: number | null) {
      return prettyFileSize(value);
    },
    setDeleteTarget(td: TrainingData) {
      this.deleteDialog = true;
      this.deleteTargetId = td;
    },
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
          console.log(progressEvent);
          var percentCompleted = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total
          );
          this.uploadProgress = percentCompleted;
        },
      };
      const result = await postWithRefreshToken<FormData, TrainingData>(
        AuthModule,
        trainingDataListURL,
        data,
        config
      ).catch((error: AxiosError) => {
        console.log(error);
        this.uploadErrorMessages = [error.response?.data.detail];
        this.uploadProgress = null;
        return undefined;
      });
      console.log(result);
      if (result) {
        this.uploadDialog = false;
        this.uploadProgress = null;
        await this.fetchData();
      } else {
        alert("Failed to upload the data.");
      }
    },

    async fetchData() {
      if (this.projectId === null) {
        return;
      }
      let trainingData = await getWithRefreshToken<responseType>(
        AuthModule,
        trainingDataListURL +
          `?${qs.stringify({
            project: this.projectId,
            page: this.pageNumber,
            page_size: pageSize,
          })}`
      ).catch((error: AxiosError) => {
        console.log(error.response?.data);
        return null;
      });
      if (trainingData?.results !== undefined) {
        this.trainingData = trainingData.results;
        this.totalSize = trainingData.count || null;
      }
    },
  },
  async mounted() {
    await this.fetchData();
  },

  components: {
    ValidationObserver,
    ValidationProvider,
  },
});
</script>
