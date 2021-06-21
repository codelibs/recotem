<template>
  <div v-if="dataBasicInfo !== null">
    <div class="pb-4 d-flex align-end">
      <div>
        <div class="text-h5">
          Data {{ dataBasicInfo.basename }}
          <span class="text-subtitle-1"> (id: {{ dataBasicInfo.id }}) </span>
          <v-dialog v-model="deleteDialog" max-width="800">
            <template v-slot:activator="{ on, attrs }">
              <v-btn icon v-on="on" v-bind="attrs">
                <v-icon> mdi-delete</v-icon>
              </v-btn>
            </template>
            <v-card>
              <v-container>
                <div>
                  <div class="text-h5 text-center">
                    Delete {{ dataBasicInfo.basename }}?
                  </div>
                  <div class="text-center text-subtitle-2">
                    This cannot be undone.
                  </div>
                </div>
                <div class="d-flex pa-8">
                  <div class="flex-grow-1"></div>
                  <v-btn color="red" dark text @click="deleteData">
                    <v-icon>mdi-delete</v-icon> DELETE
                  </v-btn>
                  <div class="flex-grow-1"></div>
                </div>
              </v-container>
            </v-card>
          </v-dialog>
        </div>
        <div class="text-subtitle-1">
          Saved as {{ dataBasicInfo.file }}
          <span v-if="dataBasicInfo.filesize !== null">
            , {{ prettyFileSize }}
          </span>
        </div>
      </div>
      <div class="flex-grow-1"></div>
      <div></div>
    </div>
    <router-view> </router-view>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import { components, paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
import { getWithRefreshToken, deleteWithRefreshToken } from "@/utils";
import { prettyFileSize } from "@/utils/conversion";

//import TuningJobList from "@/components/TuningJobList.vue";
type TrainingDataType = components["schemas"]["TrainingData"];

const retrieveURL = "/api/training_data/";
type responses = paths["/api/training_data/{id}/"]["get"]["responses"];
type respose200 = responses["200"]["content"]["application/json"];

type Data = {
  dataBasicInfo: respose200 | null;
  deleteDialog: boolean;
};
export default Vue.extend({
  data(): Data {
    return {
      dataBasicInfo: null,
      deleteDialog: false,
    };
  },
  async mounted() {
    await this.fetchTrainingData();
  },
  watch: {
    async projectId() {
      await this.fetchTrainingData();
    },
  },
  methods: {
    async deleteData(): Promise<void> {
      const url = `/api/training_data/${this.dataId}/unlink_file/`;
      let result = await deleteWithRefreshToken(AuthModule, url, undefined);
      this.deleteDialog = false;
      this.$router.push({ name: "data-list" });
    },
    async fetchTrainingData(): Promise<void> {
      if (this.dataId === null) return;
      let result = await getWithRefreshToken<TrainingDataType>(
        AuthModule,
        `${retrieveURL}/${this.dataId}`
      );
      if (result === null) return;
      console.log(result);
      this.dataBasicInfo = result;
    },
  },
  computed: {
    dataId(): number | null {
      try {
        return parseInt(this.$route.params.dataId);
      } catch {
        return null;
      }
    },
    prettyFileSize(): string {
      return prettyFileSize(this.dataBasicInfo?.filesize || null);
    },
  },
});
</script>
