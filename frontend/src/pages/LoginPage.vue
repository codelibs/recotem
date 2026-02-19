<template>
  <div class="bg-white rounded-lg shadow-md p-8">
    <h2 class="text-xl font-semibold text-neutral-800 mb-6">
      {{ t('auth.signIn') }}
    </h2>
    <form
      class="space-y-4"
      novalidate
      @submit.prevent="handleLogin"
    >
      <FormField
        :label="t('auth.username')"
        name="username"
        :error="usernameError"
        required
      >
        <template #default="{ id, hasError }">
          <InputText
            :id="id"
            v-model="username"
            class="w-full"
            :placeholder="t('auth.enterUsername')"
            :invalid="hasError || !!errorMsg"
            autocomplete="username"
            aria-required="true"
            :aria-describedby="hasError ? `${id}-error` : undefined"
            @blur="validateUsername"
          />
        </template>
      </FormField>
      <FormField
        :label="t('auth.password')"
        name="password"
        :error="passwordError"
        required
      >
        <template #default="{ id, hasError }">
          <Password
            :id="id"
            v-model="password"
            class="w-full"
            :feedback="false"
            toggle-mask
            :placeholder="t('auth.enterPassword')"
            :invalid="hasError || !!errorMsg"
            autocomplete="current-password"
            aria-required="true"
            :aria-describedby="hasError ? `${id}-error` : undefined"
            @blur="validatePassword"
          />
        </template>
      </FormField>
      <Message
        v-if="errorMsg"
        severity="error"
        :closable="false"
        role="alert"
      >
        {{ errorMsg }}
      </Message>
      <Button
        type="submit"
        :label="t('auth.signIn')"
        :loading="loading"
        class="w-full"
      />
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import InputText from "primevue/inputtext";
import Password from "primevue/password";
import Button from "primevue/button";
import Message from "primevue/message";
import { useAuthStore } from "@/stores/auth";
import FormField from "@/components/common/FormField.vue";
import { isSafeRedirect } from "@/router/index";

const { t } = useI18n();
const authStore = useAuthStore();
const router = useRouter();
const route = useRoute();
const username = ref("");
const password = ref("");
const loading = ref(false);
const errorMsg = ref("");
const usernameError = ref("");
const passwordError = ref("");

function validateUsername() {
  usernameError.value = username.value.trim() ? "" : t('auth.usernameRequired');
}

function validatePassword() {
  passwordError.value = password.value ? "" : t('auth.passwordRequired');
}

async function handleLogin() {
  validateUsername();
  validatePassword();
  if (usernameError.value || passwordError.value) return;

  loading.value = true;
  errorMsg.value = "";
  try {
    await authStore.login(username.value, password.value);
    const redirect = route.query.redirect as string;
    router.push(isSafeRedirect(redirect) ? redirect : "/projects");
  } catch {
    errorMsg.value = t('auth.invalidCredentials');
  } finally {
    loading.value = false;
  }
}
</script>
