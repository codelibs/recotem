<template>
  <v-dialog v-model="uploadDialog" max-width="800">
    <template v-slot:activator="{ on, attrs }">
      <v-btn class="mr-4" :color="color" dark v-on="on" v-bind="attrs">
        <v-icon> mdi-upload</v-icon> Upload
      </v-btn>
    </template>
    <v-card>
      <FileUpload
        v-model="createdFileId"
        :postURL="postURL"
        :fileLabel="fileLabel"
      ></FileUpload>
    </v-card>
  </v-dialog>
</template>
<script lang="ts">
import Vue, { PropType } from "vue";
import FileUpload from "@/components/FileUpload.vue";
type Data = {
  uploadDialog: boolean;
  createdFileId: number | null;
};

export default Vue.extend({
  props: {
    value: {
      type: Boolean as PropType<boolean>,
      default: false,
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
      uploadDialog: this.value,
      createdFileId: null,
    };
  },
  watch: {
    uploadDialog(nv: boolean) {
      this.$emit("input", nv);
    },
    createdFileId(nv: string | null, ov: string | null) {
      if (nv !== null) {
        this.uploadDialog = false;
      }
    },
  },
  components: {
    FileUpload,
  },
});
</script>
