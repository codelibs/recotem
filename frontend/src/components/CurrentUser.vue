<template>
  <v-menu v-if="username !== null">
    <template v-slot:activator="{ on }">
      <v-btn icon v-on="on" class="mr-1">
        <v-avatar color="brown" size="32">
          <span class="white--text title">{{ userInitial }}</span>
        </v-avatar>
      </v-btn>
    </template>
    <v-card>
      <v-list>
        <v-list-item>
          <v-list-item-content class="justify-center">
            <v-list-item-title class="text-center">
              {{ username }}
            </v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item>
          <v-btn @click="logout" depressed rounded text> logout </v-btn>
        </v-list-item>
      </v-list>
    </v-card>
  </v-menu>
</template>

<script lang="ts">
import Vue from "vue";
import { AuthModule } from "@/store/auth";
import { logout } from "@/utils/request";
export default Vue.extend({
  computed: {
    username(): string | null {
      return AuthModule.username;
    },
    userInitial(): string | null {
      if (this.username === null) return null;
      if (this.username.length === 0) return null;
      return this.username.slice(0, 1);
    },
  },
  methods: {
    async logout(): Promise<void> {
      await logout(AuthModule, this.$router);
    },
  },
});
</script>
