<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        API Keys
      </h2>
      <Button
        label="New API Key"
        icon="pi pi-plus"
        @click="showCreateDialog = true"
      />
    </div>

    <Message
      v-if="error"
      severity="error"
      :closable="false"
      class="mb-4"
    >
      <div class="flex items-center gap-2">
        <span>{{ error.message ?? "Failed to load API keys." }}</span>
        <Button
          label="Retry"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchKeys"
        />
      </div>
    </Message>

    <div
      v-if="loading"
      class="space-y-3"
    >
      <Skeleton
        v-for="i in 5"
        :key="i"
        height="3rem"
      />
    </div>

    <EmptyState
      v-else-if="!error && keys.length === 0"
      icon="pi-key"
      title="No API keys"
      description="Create an API key to access the recommendation API."
    >
      <Button
        label="New API Key"
        icon="pi pi-plus"
        @click="showCreateDialog = true"
      />
    </EmptyState>

    <div
      v-else
      class="overflow-x-auto"
    >
      <DataTable
        :value="keys"
        striped-rows
        paginator
        :rows="20"
      >
        <Column
          field="name"
          header="Name"
          sortable
        />
        <Column
          field="key_prefix"
          header="Prefix"
        >
          <template #body="{ data }">
            <code class="text-sm bg-neutral-20 px-1.5 py-0.5 rounded">{{ data.key_prefix }}...</code>
          </template>
        </Column>
        <Column header="Scopes">
          <template #body="{ data }">
            <div class="flex flex-wrap gap-1">
              <Tag
                v-for="scope in data.scopes"
                :key="scope"
                :value="scope"
                severity="info"
              />
            </div>
          </template>
        </Column>
        <Column header="Status">
          <template #body="{ data }">
            <Tag
              :value="data.is_active ? 'Active' : 'Revoked'"
              :severity="data.is_active ? 'success' : 'danger'"
            />
          </template>
        </Column>
        <Column
          field="last_used_at"
          header="Last Used"
          sortable
        >
          <template #body="{ data }">
            {{ data.last_used_at ? formatDate(data.last_used_at) : 'Never' }}
          </template>
        </Column>
        <Column
          field="ins_datetime"
          header="Created"
          sortable
        >
          <template #body="{ data }">
            {{ formatDate(data.ins_datetime) }}
          </template>
        </Column>
        <Column
          header="Actions"
          :style="{ width: '120px' }"
        >
          <template #body="{ data }">
            <Button
              v-if="data.is_active"
              icon="pi pi-ban"
              severity="warn"
              text
              rounded
              aria-label="Revoke key"
              @click="confirmRevoke(data)"
            />
            <Button
              icon="pi pi-trash"
              severity="danger"
              text
              rounded
              aria-label="Delete key"
              @click="confirmDelete(data)"
            />
          </template>
        </Column>
      </DataTable>
    </div>

    <!-- Create Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      header="New API Key"
      :modal="true"
      class="w-[480px]"
    >
      <div class="flex flex-col gap-4">
        <div>
          <label
            for="key-name"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Name</label>
          <InputText
            id="key-name"
            v-model="createForm.name"
            class="w-full"
            placeholder="e.g., Production API"
          />
        </div>
        <div>
          <label class="block text-sm font-medium text-neutral-700 mb-1">Scopes</label>
          <MultiSelect
            v-model="createForm.scopes"
            :options="availableScopes"
            placeholder="Select scopes"
            class="w-full"
          />
        </div>
        <div>
          <label
            for="key-expires"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >Expires At (optional)</label>
          <DatePicker
            id="key-expires"
            v-model="createForm.expires_at"
            class="w-full"
            show-time
            :min-date="new Date()"
          />
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <Button
            label="Cancel"
            severity="secondary"
            @click="showCreateDialog = false"
          />
          <Button
            label="Create"
            :disabled="!createForm.name || createForm.scopes.length === 0"
            :loading="creating"
            @click="handleCreate"
          />
        </div>
      </template>
    </Dialog>

    <!-- Key Reveal Dialog -->
    <Dialog
      v-model:visible="showKeyReveal"
      header="API Key Created"
      :modal="true"
      :closable="true"
      class="w-[520px]"
    >
      <Message
        severity="warn"
        :closable="false"
        class="mb-4"
      >
        Copy this key now. It will not be shown again.
      </Message>
      <div class="flex items-center gap-2 bg-neutral-20 p-3 rounded">
        <code class="flex-1 text-sm break-all">{{ revealedKey }}</code>
        <Button
          icon="pi pi-copy"
          text
          rounded
          aria-label="Copy key"
          @click="copyKey"
        />
      </div>
    </Dialog>

    <!-- Revoke Confirmation -->
    <ConfirmDialog
      v-model:visible="showRevokeConfirm"
      header="Revoke API Key"
      :message="`Are you sure you want to revoke '${revokeTarget?.name}'? This key will immediately stop working.`"
      confirm-label="Revoke"
      cancel-label="Cancel"
      danger
      @confirm="executeRevoke"
    />

    <!-- Delete Confirmation -->
    <ConfirmDialog
      v-model:visible="showDeleteConfirm"
      header="Delete API Key"
      :message="`Are you sure you want to delete '${deleteTarget?.name}'? This cannot be undone.`"
      confirm-label="Delete"
      cancel-label="Cancel"
      danger
      @confirm="executeDelete"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import MultiSelect from "primevue/multiselect";
import DatePicker from "primevue/datepicker";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { classifyApiError, unwrapResults } from "@/api/client";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import type { ClassifiedApiError } from "@/types";
import type { ApiKey } from "@/types/production";
import { getApiKeys, createApiKey, revokeApiKey, deleteApiKey } from "@/api/production";

const route = useRoute();
const notify = useNotification();
const { signal } = useAbortOnUnmount();
const projectId = Number(route.params.projectId);

const keys = ref<ApiKey[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);

const showCreateDialog = ref(false);
const showKeyReveal = ref(false);
const revealedKey = ref("");
const creating = ref(false);
const createForm = ref({
  name: "",
  scopes: [] as string[],
  expires_at: null as Date | null,
});
const availableScopes = ["recommend", "predict", "batch", "admin"];

const showRevokeConfirm = ref(false);
const revokeTarget = ref<ApiKey | null>(null);
const showDeleteConfirm = ref(false);
const deleteTarget = ref<ApiKey | null>(null);

async function fetchKeys() {
  loading.value = true;
  error.value = null;
  try {
    const res = await getApiKeys(projectId, signal);
    keys.value = unwrapResults(res);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

onMounted(fetchKeys);

async function handleCreate() {
  creating.value = true;
  try {
    const payload: { project: number; name: string; scopes: string[]; expires_at?: string } = {
      project: projectId,
      name: createForm.value.name,
      scopes: createForm.value.scopes,
    };
    if (createForm.value.expires_at) {
      payload.expires_at = createForm.value.expires_at.toISOString();
    }
    const res = await createApiKey(payload);
    revealedKey.value = res.key;
    showCreateDialog.value = false;
    showKeyReveal.value = true;
    createForm.value = { name: "", scopes: [], expires_at: null };
    await fetchKeys();
  } catch {
    notify.error("Failed to create API key.");
  } finally {
    creating.value = false;
  }
}

function copyKey() {
  navigator.clipboard.writeText(revealedKey.value);
  notify.success("Key copied to clipboard.");
}

function confirmRevoke(key: ApiKey) {
  revokeTarget.value = key;
  showRevokeConfirm.value = true;
}

async function executeRevoke() {
  if (!revokeTarget.value) return;
  try {
    await revokeApiKey(revokeTarget.value.id);
    notify.success("API key revoked.");
    await fetchKeys();
  } catch {
    notify.error("Failed to revoke API key.");
  }
  revokeTarget.value = null;
}

function confirmDelete(key: ApiKey) {
  deleteTarget.value = key;
  showDeleteConfirm.value = true;
}

async function executeDelete() {
  if (!deleteTarget.value) return;
  try {
    await deleteApiKey(deleteTarget.value.id);
    keys.value = keys.value.filter(k => k.id !== deleteTarget.value!.id);
    notify.success("API key deleted.");
  } catch {
    notify.error("Failed to delete API key.");
  }
  deleteTarget.value = null;
}
</script>
