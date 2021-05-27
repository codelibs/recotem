<template>
  <v-container>
    <v-row>
      <v-col cols="6">
        <v-card>
          <v-card-title primary-title> Recotem Login </v-card-title>
          <v-card-text>
            <v-form @keyup.enter="submit">
              <v-text-field
                @keyup.enter="submit"
                v-model="username"
                name="username"
                label="Username"
                id="username"
                type="text"
              ></v-text-field>
              <v-text-field
                @keyup.enter="submit"
                v-model="password"
                name="password"
                label="Password"
                id="password"
                type="password"
              ></v-text-field>
            </v-form>
          </v-card-text>
          <v-card-actions>
            <v-btn color="primary" @click.prevent="submit">Login</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>
    <v-row>
      <v-btn color="success" @click="getProjectList">text</v-btn>
    </v-row>
  </v-container>
</template>

<script lang="ts">
import Vue from "vue";
import { AuthModule, Auth } from "@/store/auth";
import Axios, { AxiosError } from "axios";

async function getWithRefreshToken<Return>(
  module: Auth,
  path: string
): Promise<Return | null> {
  const result = await Axios.get<Return>(path, {
    headers: { Authorization: `Bearer ${module.token}` },
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 403) {
      try {
        await module.refreshToken();
        const result = await Axios.get<Return>(path, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
    }
    return null;
  });
  if (result === null) {
    module.logout();
    return null;
  } else {
    return result.data;
  }
}

export default Vue.extend({
  data: () => ({
    username: "",
    password: "",
    error: "",
  }),
  methods: {
    async submit() {
      AuthModule.login({
        username: this.username,
        password: this.password,
      });
    },
    async getProjectList() {
      const result = await getWithRefreshToken(AuthModule, "/api/project/");
      console.log(result);
    },
  },
});
</script>
