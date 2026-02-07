<template>
  <form
    class="space-y-4"
    @submit.prevent="handleSubmit"
    novalidate
  >
    <FormField label="Project Name" name="project-name" :error="errors.name" required>
      <template #default="{ id, hasError }">
        <InputText
          :id="id"
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
    <FormField label="User Column" name="user-column" :error="errors.user_column" required>
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
    <FormField label="Item Column" name="item-column" :error="errors.item_column" required>
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
    <FormField label="Time Column" name="time-column" hint="Optional: column containing timestamps">
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
import { reactive, ref } from "vue";
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

async function handleSubmit() {
  if (!validateAll()) return;

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
  } catch {
    errorMsg.value = "Failed to create project";
  } finally {
    loading.value = false;
  }
}
</script>
