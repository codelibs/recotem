<template>
  <div class="max-w-md mx-auto">
    <h2 class="text-xl font-bold text-neutral-800 mb-6">
      {{ $t('password.changeTitle') }}
    </h2>

    <div class="flex flex-col gap-4">
      <div>
        <label
          for="current-password"
          class="block text-sm font-medium text-neutral-700 mb-1"
        >{{ $t('password.currentPassword') }}</label>
        <Password
          id="current-password"
          v-model="form.oldPassword"
          class="w-full"
          toggle-mask
          :feedback="false"
          input-class="w-full"
        />
      </div>
      <div>
        <label
          for="new-password"
          class="block text-sm font-medium text-neutral-700 mb-1"
        >{{ $t('password.newPassword') }}</label>
        <Password
          id="new-password"
          v-model="form.newPassword"
          class="w-full"
          toggle-mask
          :feedback="false"
          input-class="w-full"
        />
      </div>
      <div>
        <label
          for="confirm-password"
          class="block text-sm font-medium text-neutral-700 mb-1"
        >{{ $t('password.confirmNewPassword') }}</label>
        <Password
          id="confirm-password"
          v-model="form.confirmPassword"
          class="w-full"
          toggle-mask
          :feedback="false"
          input-class="w-full"
        />
      </div>

      <Message
        v-if="errorMessage"
        severity="error"
        :closable="false"
      >
        {{ errorMessage }}
      </Message>

      <Message
        v-if="successMessage"
        severity="success"
        :closable="false"
      >
        {{ successMessage }}
      </Message>

      <Button
        :label="$t('password.changeTitle')"
        :disabled="!canSubmit"
        :loading="submitting"
        @click="handleSubmit"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";
import Button from "primevue/button";
import Password from "primevue/password";
import Message from "primevue/message";
import { classifyApiError } from "@/api/client";
import { changeOwnPassword } from "@/api/users";

const { t } = useI18n();

const form = ref({
  oldPassword: "",
  newPassword: "",
  confirmPassword: "",
});

const submitting = ref(false);
const errorMessage = ref("");
const successMessage = ref("");

const canSubmit = computed(() =>
  form.value.oldPassword &&
  form.value.newPassword &&
  form.value.confirmPassword &&
  form.value.newPassword === form.value.confirmPassword
);

async function handleSubmit() {
  errorMessage.value = "";
  successMessage.value = "";

  if (form.value.newPassword !== form.value.confirmPassword) {
    errorMessage.value = t("password.passwordMismatch");
    return;
  }

  submitting.value = true;
  try {
    await changeOwnPassword(form.value.oldPassword, form.value.newPassword);
    successMessage.value = t("password.changeSuccess");
    form.value = { oldPassword: "", newPassword: "", confirmPassword: "" };
  } catch (e) {
    const err = classifyApiError(e);
    if (err.fieldErrors) {
      errorMessage.value = Object.values(err.fieldErrors).flat().join(" ");
    } else {
      errorMessage.value = err.message;
    }
  } finally {
    submitting.value = false;
  }
}
</script>
