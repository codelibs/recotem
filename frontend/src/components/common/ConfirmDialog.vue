<template>
  <Dialog
    v-model:visible="visible"
    :header="header"
    :modal="true"
    :closable="true"
    class="w-[400px]"
    role="alertdialog"
    :aria-label="header"
  >
    <p class="text-neutral-500">
      {{ message }}
    </p>
    <template #footer>
      <div class="flex justify-end gap-2">
        <Button
          :label="cancelLabel"
          severity="secondary"
          @click="handleCancel"
        />
        <Button
          :label="confirmLabel"
          :severity="danger ? 'danger' : 'primary'"
          autofocus
          @click="handleConfirm"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { ref } from "vue";
import Dialog from "primevue/dialog";
import Button from "primevue/button";

defineProps<{
  header?: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}>();

const emit = defineEmits<{
  confirm: [];
  cancel: [];
}>();

const visible = defineModel<boolean>("visible", { default: false });

function handleConfirm() {
  visible.value = false;
  emit("confirm");
}

function handleCancel() {
  visible.value = false;
  emit("cancel");
}
</script>
