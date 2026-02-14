<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-neutral-800">
        {{ $t('users.title') }}
      </h2>
      <Button
        :label="$t('users.newUser')"
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
        <span>{{ error.message ?? $t('users.failedToLoad') }}</span>
        <Button
          :label="$t('common.retry')"
          icon="pi pi-refresh"
          text
          size="small"
          @click="fetchUsers"
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

    <div
      v-else-if="!error"
      class="overflow-x-auto"
    >
      <DataTable
        :value="users"
        striped-rows
        paginator
        :rows="20"
      >
        <Column
          field="username"
          :header="$t('users.username')"
          sortable
        />
        <Column
          field="email"
          :header="$t('users.email')"
          sortable
        />
        <Column :header="$t('users.role')">
          <template #body="{ data }">
            <Tag
              :value="data.is_staff ? $t('users.admin') : $t('users.user')"
              :severity="data.is_staff ? 'warn' : 'info'"
            />
          </template>
        </Column>
        <Column :header="$t('common.status')">
          <template #body="{ data }">
            <Tag
              :value="data.is_active ? $t('users.active') : $t('users.inactive')"
              :severity="data.is_active ? 'success' : 'danger'"
            />
          </template>
        </Column>
        <Column
          field="date_joined"
          :header="$t('users.joined')"
          sortable
        >
          <template #body="{ data }">
            {{ formatDate(data.date_joined) }}
          </template>
        </Column>
        <Column
          field="last_login"
          :header="$t('users.lastLogin')"
          sortable
        >
          <template #body="{ data }">
            {{ data.last_login ? formatDate(data.last_login) : '-' }}
          </template>
        </Column>
        <Column
          :header="$t('common.actions')"
          :style="{ width: '150px' }"
        >
          <template #body="{ data }">
            <Button
              v-if="data.is_active"
              v-tooltip.top="$t('users.deactivate')"
              icon="pi pi-ban"
              severity="warn"
              text
              rounded
              :aria-label="$t('users.deactivate')"
              @click="confirmDeactivate(data)"
            />
            <Button
              v-else
              v-tooltip.top="$t('users.activate')"
              icon="pi pi-check-circle"
              severity="success"
              text
              rounded
              :aria-label="$t('users.activate')"
              @click="handleActivate(data)"
            />
            <Button
              v-tooltip.top="$t('users.resetPassword')"
              icon="pi pi-key"
              severity="info"
              text
              rounded
              :aria-label="$t('users.resetPassword')"
              @click="openResetDialog(data)"
            />
          </template>
        </Column>
      </DataTable>
    </div>

    <!-- Create User Dialog -->
    <Dialog
      v-model:visible="showCreateDialog"
      :header="$t('users.newUser')"
      :modal="true"
      class="w-[480px]"
    >
      <div class="flex flex-col gap-4">
        <div>
          <label
            for="new-username"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >{{ $t('users.username') }}</label>
          <InputText
            id="new-username"
            v-model="createForm.username"
            class="w-full"
          />
        </div>
        <div>
          <label
            for="new-email"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >{{ $t('users.email') }}</label>
          <InputText
            id="new-email"
            v-model="createForm.email"
            class="w-full"
            type="email"
          />
        </div>
        <div>
          <label
            for="new-password"
            class="block text-sm font-medium text-neutral-700 mb-1"
          >{{ $t('users.password') }}</label>
          <Password
            id="new-password"
            v-model="createForm.password"
            class="w-full"
            toggle-mask
            :feedback="false"
            input-class="w-full"
          />
        </div>
        <div class="flex items-center gap-2">
          <ToggleSwitch v-model="createForm.is_staff" />
          <label class="text-sm text-neutral-700">{{ $t('users.admin') }}</label>
        </div>
      </div>
      <Message
        v-if="createError"
        severity="error"
        :closable="false"
        class="mt-4"
      >
        {{ createError }}
      </Message>
      <template #footer>
        <div class="flex justify-end gap-2">
          <Button
            :label="$t('common.cancel')"
            severity="secondary"
            @click="showCreateDialog = false"
          />
          <Button
            :label="$t('common.create')"
            :disabled="!createForm.username || !createForm.password"
            :loading="creating"
            @click="handleCreate"
          />
        </div>
      </template>
    </Dialog>

    <!-- Reset Password Dialog -->
    <Dialog
      v-model:visible="showResetDialog"
      :header="$t('users.resetPassword')"
      :modal="true"
      class="w-[420px]"
    >
      <p class="mb-4 text-sm text-neutral-600">
        {{ $t('users.resetPasswordFor', { username: resetTarget?.username }) }}
      </p>
      <div>
        <label
          for="reset-password"
          class="block text-sm font-medium text-neutral-700 mb-1"
        >{{ $t('password.newPassword') }}</label>
        <Password
          id="reset-password"
          v-model="resetPassword"
          class="w-full"
          toggle-mask
          :feedback="false"
          input-class="w-full"
        />
      </div>
      <Message
        v-if="resetError"
        severity="error"
        :closable="false"
        class="mt-4"
      >
        {{ resetError }}
      </Message>
      <template #footer>
        <div class="flex justify-end gap-2">
          <Button
            :label="$t('common.cancel')"
            severity="secondary"
            @click="showResetDialog = false"
          />
          <Button
            :label="$t('users.resetPassword')"
            :disabled="!resetPassword"
            :loading="resetting"
            @click="handleResetPassword"
          />
        </div>
      </template>
    </Dialog>

    <!-- Deactivate Confirmation -->
    <ConfirmDialog
      v-model:visible="showDeactivateConfirm"
      :header="$t('users.deactivate')"
      :message="$t('users.deactivateConfirm', { username: deactivateTarget?.username })"
      :confirm-label="$t('users.deactivate')"
      :cancel-label="$t('common.cancel')"
      danger
      @confirm="handleDeactivate"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import DataTable from "primevue/datatable";
import Column from "primevue/column";
import Button from "primevue/button";
import Message from "primevue/message";
import Skeleton from "primevue/skeleton";
import Tag from "primevue/tag";
import Dialog from "primevue/dialog";
import InputText from "primevue/inputtext";
import Password from "primevue/password";
import ToggleSwitch from "primevue/toggleswitch";
import Tooltip from "primevue/tooltip";
import { formatDate } from "@/utils/format";
import { useNotification } from "@/composables/useNotification";
import { useAbortOnUnmount } from "@/composables/useAbortOnUnmount";
import { classifyApiError } from "@/api/client";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import type { ClassifiedApiError, ManagedUser } from "@/types";
import {
  getUsers,
  createUser,
  deactivateUser,
  activateUser,
  resetUserPassword,
} from "@/api/users";

const vTooltip = Tooltip;

const notify = useNotification();
const { signal } = useAbortOnUnmount();

const users = ref<ManagedUser[]>([]);
const loading = ref(false);
const error = ref<ClassifiedApiError | null>(null);

// Create
const showCreateDialog = ref(false);
const creating = ref(false);
const createError = ref("");
const createForm = ref({
  username: "",
  email: "",
  password: "",
  is_staff: false,
});

// Deactivate
const showDeactivateConfirm = ref(false);
const deactivateTarget = ref<ManagedUser | null>(null);

// Reset password
const showResetDialog = ref(false);
const resetTarget = ref<ManagedUser | null>(null);
const resetPassword = ref("");
const resetting = ref(false);
const resetError = ref("");

async function fetchUsers() {
  loading.value = true;
  error.value = null;
  try {
    users.value = await getUsers(signal);
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      error.value = classifyApiError(e);
    }
  } finally {
    loading.value = false;
  }
}

onMounted(fetchUsers);

async function handleCreate() {
  creating.value = true;
  createError.value = "";
  try {
    await createUser(createForm.value);
    showCreateDialog.value = false;
    createForm.value = { username: "", email: "", password: "", is_staff: false };
    notify.success("User created successfully.");
    await fetchUsers();
  } catch (e) {
    const err = classifyApiError(e);
    if (err.fieldErrors) {
      createError.value = Object.values(err.fieldErrors).flat().join(" ");
    } else {
      createError.value = err.message;
    }
  } finally {
    creating.value = false;
  }
}

function confirmDeactivate(user: ManagedUser) {
  deactivateTarget.value = user;
  showDeactivateConfirm.value = true;
}

async function handleDeactivate() {
  if (!deactivateTarget.value) return;
  try {
    await deactivateUser(deactivateTarget.value.id);
    notify.success("User deactivated.");
    await fetchUsers();
  } catch (e) {
    const err = classifyApiError(e);
    notify.error(err.message);
  }
  deactivateTarget.value = null;
}

async function handleActivate(user: ManagedUser) {
  try {
    await activateUser(user.id);
    notify.success("User activated.");
    await fetchUsers();
  } catch {
    notify.error("Failed to activate user.");
  }
}

function openResetDialog(user: ManagedUser) {
  resetTarget.value = user;
  resetPassword.value = "";
  resetError.value = "";
  showResetDialog.value = true;
}

async function handleResetPassword() {
  if (!resetTarget.value || !resetPassword.value) return;
  resetting.value = true;
  resetError.value = "";
  try {
    await resetUserPassword(resetTarget.value.id, resetPassword.value);
    showResetDialog.value = false;
    notify.success("Password has been reset.");
  } catch (e) {
    const err = classifyApiError(e);
    if (err.fieldErrors) {
      resetError.value = Object.values(err.fieldErrors).flat().join(" ");
    } else {
      resetError.value = err.message;
    }
  } finally {
    resetting.value = false;
  }
}
</script>
