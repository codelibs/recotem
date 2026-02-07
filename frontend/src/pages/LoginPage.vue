<template>
  <div class="bg-white rounded-lg shadow-md p-8">
    <h2 class="text-xl font-semibold text-neutral-800 mb-6">
      Sign in
    </h2>
    <form
      class="space-y-4"
      @submit.prevent="handleLogin"
      novalidate
    >
      <FormField label="Username" name="username" :error="usernameError" required>
        <template #default="{ id, hasError }">
          <InputText
            :id="id"
            v-model="username"
            class="w-full"
            placeholder="Enter username"
            :invalid="hasError || !!errorMsg"
            autocomplete="username"
            aria-required="true"
            :aria-describedby="hasError ? `${id}-error` : undefined"
            @blur="validateUsername"
          />
        </template>
      </FormField>
      <FormField label="Password" name="password" :error="passwordError" required>
        <template #default="{ id, hasError }">
          <Password
            :id="id"
            v-model="password"
            class="w-full"
            :feedback="false"
            toggle-mask
            placeholder="Enter password"
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
        label="Sign in"
        :loading="loading"
        class="w-full"
      />
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import InputText from "primevue/inputtext";
import Password from "primevue/password";
import Button from "primevue/button";
import Message from "primevue/message";
import { useAuthStore } from "@/stores/auth";
import FormField from "@/components/common/FormField.vue";

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
  usernameError.value = username.value.trim() ? "" : "Username is required";
}

function validatePassword() {
  passwordError.value = password.value ? "" : "Password is required";
}

async function handleLogin() {
  validateUsername();
  validatePassword();
  if (usernameError.value || passwordError.value) return;

  loading.value = true;
  errorMsg.value = "";
  try {
    await authStore.login(username.value, password.value);
    router.push((route.query.redirect as string) || "/projects");
  } catch {
    errorMsg.value = "Invalid username or password";
  } finally {
    loading.value = false;
  }
}
</script>
