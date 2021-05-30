import Vue from "vue";
import VueRouter, { RouteConfig } from "vue-router";
import Login from "../views/Login.vue";

Vue.use(VueRouter);

const routes: Array<RouteConfig> = [
  {
    path: "/",
    component: () => import("../views/Main.vue"),
    children: [
      {
        path: "",
        redirect: { name: "project" },
      },
      {
        path: "login",
        name: "login",
        component: Login,
      },
      {
        path: "project",
        name: "project",
        component: () => import("../views/project/Projects.vue"),
        children: [
          {
            path: "create",
            name: "project-create",
            component: () => import("../components/ProjectCreate.vue"),
          },
        ],
      },
    ],
  },
];

const router = new VueRouter({
  // mode: "history",
  base: process.env.BASE_URL,
  routes,
});

export default router;
