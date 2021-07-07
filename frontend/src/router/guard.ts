import { checkLogin } from "@/utils";
import { AuthModule } from "@/store/auth";
import { NavigationGuardNext, Route } from "vue-router";

export async function guard(
  to: Route,
  from: Route,
  next: NavigationGuardNext<Vue>
) {
  const loggedIn = await checkLogin(AuthModule);
  console.log("loggedIn", loggedIn);
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
