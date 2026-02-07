<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <Button
        icon="pi pi-arrow-left"
        text
        rounded
        @click="router.push(`/projects/${projectId}/data`)"
      />
      <h2 class="text-xl font-bold text-neutral-800">
        Upload Training Data
      </h2>
    </div>

    <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6 max-w-xl">
      <FileUpload
        mode="advanced"
        accept=".csv,.csv.gz"
        :max-file-size="104857600"
        :auto="false"
        choose-label="Choose File"
        :custom-upload="true"
        :disabled="uploading"
        @upload="onUpload"
        @error="onError"
        @uploader="handleUpload"
      >
        <template #empty>
          <div class="text-center py-8">
            <i class="pi pi-cloud-upload text-4xl text-neutral-40 mb-2" />
            <p class="text-neutral-200">
              Drag and drop a CSV file here
            </p>
          </div>
        </template>
      </FileUpload>

      <ProgressBar
        v-if="uploading"
        :value="progress"
        class="mt-4"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import FileUpload from "primevue/fileupload";
import ProgressBar from "primevue/progressbar";
import Button from "primevue/button";
import { toApiUrl } from "@/api/config";
import { useNotification } from "@/composables/useNotification";

const route = useRoute();
const router = useRouter();
const notify = useNotification();
const projectId = route.params.projectId as string;
const uploading = ref(false);
const progress = ref(0);

function uploadWithProgress(formData: FormData): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", toApiUrl("/training_data/"));

    const token = localStorage.getItem("access_token");
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        progress.value = Math.round((event.loaded / event.total) * 100);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Upload failed with status ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error("Upload failed"));
    xhr.send(formData);
  });
}

async function handleUpload(event: any) {
  const file = event.files[0];
  if (!file) return;

  uploading.value = true;
  progress.value = 0;

  const formData = new FormData();
  formData.append("file", file);
  formData.append("project", projectId);

  try {
    await uploadWithProgress(formData);
    progress.value = 100;
    notify.success("Data uploaded successfully");
    router.push(`/projects/${projectId}/data`);
  } catch {
    notify.error("Failed to upload data");
  } finally {
    uploading.value = false;
  }
}

function onUpload() {
  notify.success("Upload complete");
}

function onError() {
  notify.error("Upload failed");
}
</script>
