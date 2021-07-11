<template>
  <v-container>
    <v-row class="mt-6">
      <v-col cols="3"></v-col>
      <v-col cols="6">
        <ValidationObserver v-slot="{ invalid }">
          <v-card>
            <v-card-title primary-title> Recotem Login </v-card-title>
            <v-card-text>
              <v-form @keyup.enter="submit">
                <ValidationProvider
                  rules="loginRequired"
                  name="username"
                  v-slot="{ errors }"
                >
                  <v-text-field
                    @keyup.enter="submit"
                    v-model="username"
                    label="username"
                    name="username"
                    type="text"
                    :error-messages="errors"
                    required
                  ></v-text-field>
                </ValidationProvider>
                <ValidationProvider
                  name="password"
                  rules="loginRequired"
                  v-slot="{ errors }"
                >
                  <v-text-field
                    @keyup.enter="submit"
                    v-model="password"
                    label="Password"
                    type="password"
                    name="password"
                    :error-messages="errors"
                  ></v-text-field>
                </ValidationProvider>
              </v-form>
            </v-card-text>
            <v-card-actions class="pt-2 pb-8">
              <v-row justify="center">
                <v-btn
                  color="primary"
                  :disabled="invalid"
                  @click.prevent="submit"
                  name="login"
                  >Login</v-btn
                >
              </v-row>
            </v-card-actions>
            <v-alert
              v-for="(message, i_m) in errorMessages"
              type="error"
              :key="i_m"
            >
              {{ message }}
            </v-alert>
          </v-card>
        </ValidationObserver>
      </v-col>
    </v-row>
  </v-container>
</template>

<script lang="ts">
import Vue from "vue";
import { paths } from "@/api/schema.ts";
import { AuthModule } from "@/store/auth";
import { required } from "vee-validate/dist/rules";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import axios, { AxiosError } from "axios";
import { baseURL } from "@/env";
import { alertAxiosError } from "@/utils/exception";

type tokenReturn =
  paths["/api/auth/login/"]["post"]["responses"]["200"]["content"]["application/json"];
const tokenObtainUrl = `${baseURL}/api/auth/login/`;

extend("loginRequired", {
  ...required,
  message: "{_field_} required",
});

type Data = {
  username: string;
  password: string;
  errorMessages: string[];
};
export default Vue.extend({
  data(): Data {
    return {
      username: "",
      password: "",
      errorMessages: [],
    };
  },
  components: {
    ValidationProvider,
    ValidationObserver,
  },
  mounted() {
    AuthModule.resetProject();
  },
  methods: {
    async submit() {
      const p = {
        username: this.username,
        password: this.password,
        access: "",
        refresh: "",
      };

      const response = await axios
        .post<tokenReturn>(tokenObtainUrl, p, {
          xsrfCookieName: "csrftoken",
          xsrfHeaderName: "X-CSRFTOKEN",
        })
        .catch((error: AxiosError) => {
          if (error.response?.status !== 400) {
            alertAxiosError(error);
            throw error;
          }
          const errors: string[] = error.response?.data?.non_field_errors;
          this.errorMessages.splice(0, this.errorMessages.length, ...errors);
          this.username = "";
          this.password = "";
          return null;
        });
      if (response !== null) {
        AuthModule.setToken(response.data.access_token);
      } else {
        AuthModule.setToken(null);
      }

      if (AuthModule.token !== null) {
        let to = this.$route.query.redirect;
        if (typeof to === "string") {
          this.$router.push({ path: to });
        } else {
          this.$router.push({ name: "project-list" });
        }
      }
    },
  },
});
</script>
