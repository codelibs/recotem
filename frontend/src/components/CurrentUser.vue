<template>
  <v-menu v-if="username !== null">
    <template v-slot:activator="{ on }">
      <v-btn icon x-large v-on="on">
        <v-avatar color="brown" size="48">
          <span class="white--text headline">{{ userInitial }}</span>
        </v-avatar>
      </v-btn>
    </template>
    <v-card>
      <v-list-item-content class="justify-center">
        <div class="mx-auto text-center">
          <h3>{{ username }}</h3>
          <v-divider class="my-3"></v-divider>
          <v-divider class="my-3"></v-divider>
          <v-btn depressed rounded text> Disconnect </v-btn>
        </div>
      </v-list-item-content>
    </v-card>
  </v-menu>
</template>

<script lang="ts">
import Vue from "vue";
import { AuthModule } from "@/store/auth";
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
});
</script>
