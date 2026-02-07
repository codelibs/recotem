<template>
  <form
    class="space-y-4"
    novalidate
    @submit.prevent="handleSubmit"
  >
    <FormField
      label="Project Name"
      name="project-name"
      :error="errors.name"
      required
    >
      <template #default="{ id, hasError }">
        <InputText
          :id="id"
          ref="nameInput"
          v-model="form.name"
          class="w-full"
          placeholder="My Project"
          :invalid="hasError"
          aria-required="true"
          :aria-describedby="hasError ? `${id}-error` : undefined"
          @blur="validateField('name')"
        />
      </template>
    </FormField>
    <FormField
      label="User Column"
      name="user-column"
      :error="errors.user_column"
      required
    >
      <template #default="{ id, hasError }">
        <InputText
          :id="id"
          v-model="form.user_column"
          class="w-full"
          placeholder="user_id"
          :invalid="hasError"
          aria-required="true"
          :aria-describedby="hasError ? `${id}-error` : undefined"
          @blur="validateField('user_column')"
        />
      </template>
    </FormField>
    <FormField
      label="Item Column"
      name="item-column"
      :error="errors.item_column"
      required
    >
      <template #default="{ id, hasError }">
        <InputText
          :id="id"
          v-model="form.item_column"
          class="w-full"
          placeholder="item_id"
          :invalid="hasError"
          aria-required="true"
          :aria-describedby="hasError ? `${id}-error` : undefined"
          @blur="validateField('item_column')"
        />
      </template>
    </FormField>
    <FormField
      label="Time Column"
      name="time-column"
      hint="Optional: column containing timestamps"
    >
      <template #default="{ id }">
        <InputText
          :id="id"
          v-model="form.time_column"
          class="w-full"
          placeholder="timestamp"
          :aria-describedby="`${id}-hint`"
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
    <div class="flex justify-end gap-2 pt-2">
      <Button
        type="button"
        label="Cancel"
        severity="secondary"
        @click="$emit('cancel')"
      />
      <Button
        type="submit"
        label="Create"
        :loading="loading"
      />
    </div>
  </form>
</template>

<script setup lang="ts">
import { reactive, ref, nextTick } from "vue";
import InputText from "primevue/inputtext";
import Button from "primevue/button";
import Message from "primevue/message";
import { useProjectStore } from "@/stores/project";
import { useNotification } from "@/composables/useNotification";
import FormField from "@/components/common/FormField.vue";

const emit = defineEmits<{ (e: "created"): void; (e: "cancel"): void }>();
const projectStore = useProjectStore();
const notify = useNotification();
const loading = ref(false);
const errorMsg = ref("");
const nameInput = ref<InstanceType<typeof InputText> | null>(null);

const form = reactive({
  name: "",
  user_column: "",
  item_column: "",
  time_column: "",
});

const errors = reactive({
  name: "",
  user_column: "",
  item_column: "",
});

function validateField(field: keyof typeof errors) {
  const value = form[field].trim();
  if (!value) {
    errors[field] = `${field === "name" ? "Project name" : field === "user_column" ? "User column" : "Item column"} is required`;
  } else {
    errors[field] = "";
  }
}

function validateAll(): boolean {
  validateField("name");
  validateField("user_column");
  validateField("item_column");
  return !errors.name && !errors.user_column && !errors.item_column;
}

function focusFirstError() {
  const firstErrorField = ["name", "user_column", "item_column"].find(
    (f) => errors[f as keyof typeof errors]
  );
  if (firstErrorField) {
    const el = document.querySelector<HTMLInputElement>(
      `[name="${firstErrorField === "name" ? "project-name" : firstErrorField === "user_column" ? "user-column" : "item-column"}"] input, #field-${firstErrorField === "name" ? "project-name" : firstErrorField === "user_column" ? "user-column" : "item-column"}`
    );
    el?.focus();
  }
}

function applyServerErrors(err: unknown) {
  const data = (err as any)?.data;
  if (data && typeof data === "object") {
    const fieldMap: Record<string, keyof typeof errors> = {
      name: "name",
      user_column: "user_column",
      item_column: "item_column",
    };
    let hasFieldError = false;
    for (const [apiField, formField] of Object.entries(fieldMap)) {
      if (data[apiField]) {
        const msg = Array.isArray(data[apiField])
          ? data[apiField].join(". ")
          : String(data[apiField]);
        errors[formField] = msg;
        hasFieldError = true;
      }
    }
    if (hasFieldError) {
      nextTick(focusFirstError);
      return;
    }
    if (data.detail) {
      errorMsg.value = String(data.detail);
      return;
    }
    if (data.non_field_errors) {
      const msgs = Array.isArray(data.non_field_errors)
        ? data.non_field_errors.join(". ")
        : String(data.non_field_errors);
      errorMsg.value = msgs;
      return;
    }
  }
  errorMsg.value = "Failed to create project";
}

async function handleSubmit() {
  if (!validateAll()) {
    nextTick(focusFirstError);
    return;
  }

  loading.value = true;
  errorMsg.value = "";
  try {
    await projectStore.createProject({
      name: form.name,
      user_column: form.user_column,
      item_column: form.item_column,
      time_column: form.time_column || null,
    });
    notify.success("Project created");
    emit("created");
  } catch (err) {
    applyServerErrors(err);
  } finally {
    loading.value = false;
  }
}
</script>
