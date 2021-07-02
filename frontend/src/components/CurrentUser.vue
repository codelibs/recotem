<template>
  <v-menu v-if="username !== null" bottom>
    <template v-slot:activator="{ on }">
      <v-btn icon v-on="on" class="mr-1">
        <v-avatar color="brown" size="32">
          <span class="white--text title">{{ userInitial }}</span>
        </v-avatar>
      </v-btn>
    </template>
    <v-card>
      <v-list nav>
        <v-list-item>
          <v-list-item-content class="justify-center">
            <v-container fluid class="text-center">
              <v-btn icon dark small>
                <v-avatar color="brown" size="32">
                  <span class="white--text title">{{ userInitial }}</span>
                </v-avatar>
              </v-btn>
            </v-container>
            <div class="text-center">
              {{ username }}
            </div>
          </v-list-item-content>
        </v-list-item>
        <v-divider />
        <v-list-item class="justify-center" link href="/api/schema/redoc/">
          <v-list-item-title>
            <v-icon>mdi-api</v-icon>
            api schema
          </v-list-item-title>
        </v-list-item>
        <v-list-item class="justify-center" link href="/api/admin">
          <v-list-item-title>
            <v-icon>mdi-language-python</v-icon> django admin
          </v-list-item-title>
        </v-list-item>
        <v-list-item class="justify-center" link :href="docURL">
          <v-list-item-title>
            <v-icon>mdi-help</v-icon> help
          </v-list-item-title>
        </v-list-item>
        <v-list-item @click="logout">
          <v-list-item-title>
            <v-icon> mdi-logout</v-icon> logout
          </v-list-item-title>
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
    docURL(): string | null {
      if (this.$route.name === null || this.$route.name === undefined) {
        return null;
      }

      if (navigator.language === "ja") {
        return `${AuthModule.docURLBase}/ja/${AuthModule.recotemVersion}/user/${this.$route.name}.html`;
      } else {
        return `${AuthModule.docURLBase}/${AuthModule.recotemVersion}/user/${this.$route.name}.html`;
      }
    },
  },
  methods: {
    async logout(): Promise<void> {
      await logout(AuthModule, this.$router);
    },
  },
});
</script>
