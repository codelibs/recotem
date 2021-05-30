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
                    label="Username"
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
                    :error-messages="errors"
                  ></v-text-field>
                </ValidationProvider>
              </v-form>
            </v-card-text>
            <v-card-actions class="text-center">
              <v-btn color="primary" :disabled="invalid" @click.prevent="submit"
                >Login</v-btn
              >
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
import { AuthModule } from "@/store/auth";
import { required } from "vee-validate/dist/rules";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";

extend("loginRequired", {
  ...required,
  message: "{_field_} required",
});

export default Vue.extend({
  data: () => ({
    username: "",
    password: "",
    error: "",
  }),
  components: {
    ValidationProvider,
    ValidationObserver,
  },
  computed: {
    errorMessages() {
      return AuthModule.loginErrorMessages;
    },
  },
  methods: {
    async submit() {
      await AuthModule.login({
        username: this.username,
        password: this.password,
      });
      if (AuthModule.token !== null) {
        console.log(this.$route);
        let to = this.$route.query.redirect;
        if (typeof to === "string") {
          this.$router.push({ path: to });
        } else {
          this.$router.push({ path: "/projects" });
        }
      }
    },
  },
});
</script>
