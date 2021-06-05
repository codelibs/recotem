<template>
  <div class="mt-1">
    <div style="text-align: right">
      <v-dialog v-model="uploadDialog" max-width="800">
        <template v-slot:activator="{ on, attrs }">
          <v-btn class="mr-4" color="green" dark v-on="on" v-bind="attrs">
            <v-icon> mdi-upload</v-icon> Upload new data
          </v-btn>
        </template>
        <v-card>
          <v-container>
            <ValidationObserver v-slot="{ invalid }">
              <v-form>
                <ValidationProvider
                  v-slot="{ errors }"
                  rules="uploadFileRequired"
                >
                  <v-file-input
                    label="The interaction data."
                    accept=".csv,.tsv,.ndjson,.jsonl,.pkl,.pickle,.csv.gz,.tsv.gz,.ndjson.gz,.jsonl.gz,.pkl.gz,.pickle.gz"
                    :error-messages="errors"
                    v-model="uploadFile"
                  >
                  </v-file-input>
                </ValidationProvider>
                <div></div>
              </v-form>
              <v-row justify="center" class="mb-4">
                <v-btn color="primary" :disabled="invalid" @click="upload"
                  >Upload</v-btn
                >
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
    <v-container v-if="trainingData !== undefined">
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
                    {{ prettyFilesize(td.filesize) }}
                  </v-col>
                  <v-spacer></v-spacer>
                </v-row>
              </v-list-item-subtitle>
            </v-list-item-content>
            <v-list-item-action>
              <v-btn
                icon
                color="primary"
                dark
                :to="{
                  name: 'start-tuning-with-data',
                  params: { dataId: td.id },
                }"
              >
                <v-icon>mdi-tune</v-icon>
              </v-btn>
            </v-list-item-action>
          </v-list-item>
          <v-divider :key="i + 0.5"></v-divider>
        </template>
      </v-list>
    </v-container>
    <div v-else class="text-center">No data yet.</div>
    <v-dialog v-model="deleteDialog">
      <v-card v-if="deleteTargetId !== null">
        <v-container>
          Delete {{ deleteTargetId.basename }} ?
          <v-btn color="danger"> Delete </v-btn>
        </v-container>
      </v-card>
    </v-dialog>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import qs from "qs";
import { components, paths } from "@/api/schema";
import { getWithRefreshToken, postWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";
import { AxiosError } from "axios";
import { required } from "vee-validate/dist/rules";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import { ThisTypedComponentOptionsWithRecordProps } from "vue/types/options";

extend("uploadFileRequired", {
  ...required,
  message: "Upload file required",
});

const trainingDataListURL = "/api/training_data/";
type TrainingData = components["schemas"]["TrainingData"];
type responseType =
  paths["/api/training_data/"]["get"]["responses"]["200"]["content"]["application/json"];
type responseContent = responseType["results"];

const pageSize = 2;

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
    setDeleteTarget(td: TrainingData) {
      this.deleteDialog = true;
      this.deleteTargetId = td;
    },
    async upload(): Promise<void> {
      if (this.uploadFile === null) return;
      if (this.projectId === null) return;
      const data = new FormData();
      data.append("project", `${this.projectId}`);
      data.append("upload_path", this.uploadFile);
      const result = await postWithRefreshToken<any, TrainingData>(
        AuthModule,
        trainingDataListURL,
        data
      ).catch((error: AxiosError) => {
        this.uploadErrorMessages = [error.response?.data.detail];
        return undefined;
      });
      if (result) {
        this.uploadDialog = false;
        await this.fetchData();
      }
    },
    prettyFilesize(x: number | null): string {
      if (x === null) {
        return "Unknown";
      }
      if (x < 1024) {
        return `${x}B`;
      }
      if (x < 1048576) {
        return `${(x / 1024).toFixed(1)}kB`;
      }
      if (x < 1073741824) {
        return `${(x / 1048576).toFixed(1)}MB`;
      }
      return `${(x / 1073741824).toFixed(1)}MB`;
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
