import { createApp } from "vue";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import Aura from "@primevue/themes/aura";
import ToastService from "primevue/toastservice";
import ConfirmationService from "primevue/confirmationservice";
import Tooltip from "primevue/tooltip";
import App from "./App.vue";
import router from "./router";
import i18n from "./i18n";
import "./styles/main.css";
import "primeicons/primeicons.css";

const app = createApp(App);

app.config.errorHandler = (err, _instance, info) => {
  console.error(`[Vue Error] ${info}:`, err);
  const toast = app.config.globalProperties.$toast;
  if (toast) {
    const message = err instanceof Error ? err.message : String(err);
    toast.add({
      severity: "error",
      summary: i18n.global.t("errors.unknown"),
      detail: message,
      life: 10000,
    });
  }
};

app.use(createPinia());
app.use(i18n);
app.use(router);
app.use(PrimeVue, {
  theme: {
    preset: Aura,
    options: {
      darkModeSelector: ".dark-mode",
    },
  },
});
app.use(ToastService);
app.use(ConfirmationService);
app.directive("tooltip", Tooltip);

app.mount("#app");
