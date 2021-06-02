<template>
  <router-view> </router-view>
</template>

<script lang="ts">
import Vue from "vue";
import { checkLogin } from "@/utils";
import store from "@/store";
import { Auth } from "@/store/auth";
import { getModule } from "vuex-module-decorators";
import { NavigationGuardNext, Route } from "vue-router";

async function guard(to: Route, from: Route, next: NavigationGuardNext<Vue>) {
  const authModule = getModule(Auth, store);
  const loggedIn = await checkLogin(authModule);
  if (loggedIn) {
    next();
  } else {
    if (to.name === "login") {
      next();
    } else {
      next({ name: "login", query: { redirect: to.fullPath } });
    }
  }
}

export default Vue.extend({
  async beforeRouteEnter(to, from, next) {
    await guard(to, from, next);
  },
  async beforeRouteUpdate(to, from, next) {
    await guard(to, from, next);
  },
});
</script>
