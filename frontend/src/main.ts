import { createApp } from "vue";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";
import Aura from "@primevue/themes/aura";
import ToastService from "primevue/toastservice";
import ConfirmationService from "primevue/confirmationservice";
import Tooltip from "primevue/tooltip";
import App from "./App.vue";
import router from "./router";
import "./styles/main.css";
import "primeicons/primeicons.css";

const app = createApp(App);

app.config.errorHandler = (err, _instance, info) => {
  console.error(`[Vue Error] ${info}:`, err);
};

app.use(createPinia());
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
