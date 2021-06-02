<template>
  <div class="mt-4">
    <div>
      <v-row>
        <v-spacer> </v-spacer>
        <v-btn class="mr-4" color="green" dark @click="uploadDialog = true">
          <v-icon> mdi-upload</v-icon> Upload new data
        </v-btn>
        <v-dialog v-model="uploadDialog" max-width="800">
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
      </v-row>
    </div>
    <v-list v-if="trainingData.length > 0">
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
                <v-col cols="4"> {{ td.ins_datetime }} </v-col>
                <v-col cols="4">
                  {{ prettyFilesize(td.filesize) }}
                </v-col>
              </v-row>
            </v-list-item-subtitle>
          </v-list-item-content>
          <v-list-item-action>
            <v-btn icon color="primary" dark>
              <v-icon>mdi-tune</v-icon>
            </v-btn>
          </v-list-item-action>
          <v-list-item-action>
            <v-btn icon color="warning" @click="setDeleteTarget(td)">
              <v-icon>mdi-delete</v-icon>
            </v-btn>
          </v-list-item-action>
        </v-list-item>
        <v-divider
          v-if="i < trainingData.length - 1"
          :key="i + 0.5"
        ></v-divider>
      </template>
    </v-list>
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

extend("uploadFileRequired", {
  ...required,
  message: "Upload file required",
});

const trainingDataListURL = "/api/training_data/";
type TrainingData = components["schemas"]["TrainingData"];
type Data = {
  trainingData: TrainingData[];
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
      trainingData: [],
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
    prettyFilesize(x: number): string {
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
      let trainingData = await getWithRefreshToken<TrainingData[]>(
        AuthModule,
        trainingDataListURL + `?${qs.stringify({ project: this.projectId })}`
      ).catch((error: AxiosError) => {
        console.log(error.response?.data);
        return null;
      });
      if (trainingData !== null) {
        this.trainingData = trainingData;
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
