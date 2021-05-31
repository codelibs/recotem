<template>
  <router-view></router-view>
</template>

<script lang="ts">
import Vue from "vue";
import { checkLogin } from "@/utils";
import store from "@/store";
import { Auth } from "@/store/auth";
import { getModule } from "vuex-module-decorators";

export default Vue.extend({
  async beforeRouteEnter(to, from, next) {
    const authModule = getModule(Auth, store);
    const loggedIn = await checkLogin(authModule);
    console.log("login", loggedIn);
    if (loggedIn) {
      next();
    } else {
      if (to.name === "login") {
        next();
      } else {
        next({ name: "login", query: { redirect: to.fullPath } });
      }
    }
  },
});
</script>
