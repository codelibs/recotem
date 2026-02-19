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
        {{ t('data.uploadTitle') }}
      </h2>
    </div>

    <div class="bg-white rounded-lg shadow-sm border border-neutral-30 p-6 max-w-xl">
      <FileUpload
        mode="advanced"
        accept=".csv,.csv.gz"
        :max-file-size="104857600"
        :auto="false"
        :choose-label="t('data.chooseFile')"
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
              {{ t('data.dragAndDropCsv') }}
            </p>
          </div>
        </template>
      </FileUpload>

      <ProgressBar
        v-if="uploading"
        :value="progress"
        class="mt-4"
      />

      <div
        v-if="uploadError"
        class="mt-4"
      >
        <Message
          severity="error"
          :closable="false"
        >
          <div class="flex items-center gap-2">
            <span>{{ uploadError }}</span>
            <Button
              v-if="lastFile"
              :label="t('common.retry')"
              icon="pi pi-refresh"
              text
              size="small"
              @click="retryUpload"
            />
          </div>
        </Message>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import FileUpload from "primevue/fileupload";
import ProgressBar from "primevue/progressbar";
import Button from "primevue/button";
import Message from "primevue/message";
import { toApiUrl } from "@/api/config";
import { useAuthStore } from "@/stores/auth";
import { useNotification } from "@/composables/useNotification";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const notify = useNotification();
const projectId = route.params.projectId as string;
const uploading = ref(false);
const progress = ref(0);
const uploadError = ref("");
const lastFile = ref<File | null>(null);

function uploadWithProgress(file: File): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);
    formData.append("project", projectId);

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        progress.value = Math.round((e.loaded / e.total) * 100);
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        let detail = t("data.uploadFailed");
        try {
          const body = JSON.parse(xhr.responseText);
          if (Array.isArray(body)) {
            detail = body[0] ?? detail;
          } else {
            detail = body.detail ?? body.file?.[0] ?? body.project?.[0] ?? detail;
          }
        } catch { /* ignore parse errors */ }
        const err = new Error(detail) as Error & { status: number };
        err.status = xhr.status;
        reject(err);
      }
    });

    xhr.addEventListener("error", () => reject(new Error(t("data.networkError"))));
    xhr.addEventListener("abort", () => reject(new Error(t("data.uploadCancelled"))));

    xhr.open("POST", toApiUrl("/training_data/"));
    xhr.send(formData);
  });
}

async function doUpload(file: File) {
  uploading.value = true;
  progress.value = 0;
  uploadError.value = "";
  lastFile.value = file;

  try {
    await authStore.ensureFreshToken();
    await uploadWithProgress(file);
    notify.success(t("data.uploadSuccess"));
    setTimeout(() => router.push(`/projects/${projectId}/data`), 500);
  } catch (e) {
    if ((e as any).status === 401) {
      // Token expired during upload — refresh and retry once
      try {
        await authStore.refreshAccessToken();
        if (!authStore.isAuthenticated) throw e;
        progress.value = 0;
        await uploadWithProgress(file);
        notify.success(t("data.uploadSuccess"));
        setTimeout(() => router.push(`/projects/${projectId}/data`), 500);
        return;
      } catch {
        // Retry also failed
      }
    }
    uploadError.value = (e as Error).message || t("data.uploadFailed");
  } finally {
    uploading.value = false;
  }
}

async function handleUpload(event: any) {
  const file = event.files[0];
  if (!file) return;
  await doUpload(file);
}

function retryUpload() {
  if (lastFile.value) doUpload(lastFile.value);
}

function onUpload() {
  notify.success(t("data.uploadComplete"));
}

function onError() {
  notify.error(t("data.uploadFailed"));
}
</script>

<style scoped>
:deep(.p-fileupload-file-thumbnail) {
  display: none;
}
</style>
