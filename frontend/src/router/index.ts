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
        redirect: { name: "project-list" },
      },
      {
        path: "login/",
        name: "login",
        component: Login,
      },
      {
        path: "project-list/",
        name: "project-list",
        component: () => import("../views/ProjectList.vue"),
      },
      {
        path: "project/:projectId/",
        component: () => import("../views/project/ProjectTop.vue"),
        children: [
          {
            path: "/",
            name: "project",
            component: () => import("../views/project/Dashboard.vue"),
          },
          {
            path: "data-list/",
            name: "data-list",
            component: () => import("../views/project/DataList.vue"),
          },
          {
            path: "data/:dataId/",
            name: "data",
            component: () => import("../views/project/Data.vue"),
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
