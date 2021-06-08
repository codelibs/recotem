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
            path: "trained_model/",
            component: () => import("../views/project/Data.vue"),
            children: [
              {
                path: ":trainedModelId/",
                name: "trained-model-detail",
                component: () => import("../views/project/models/ModelTop.vue"),
              },
            ],
          },
          {
            path: "tuning_job/",
            component: () => import("../views/project/Data.vue"),
            children: [
              {
                path: ":parameterTuningJobId/",
                name: "tuning-job-detail",
                component: () =>
                  import("../views/project/tuningjobs/TuningJobTop.vue"),
              },
              {
                path: "",
                name: "tuning-job-list",
                component: () => import("../views/project/tuningjobs/List.vue"),
              },
            ],
          },
          {
            path: "data/",
            component: () => import("../views/project/Data.vue"),
            children: [
              {
                path: ":dataId/",
                component: () => import("../views/project/data/DataTop.vue"),
                children: [
                  {
                    path: "",
                    name: "data-detail",
                    component: () => import("../views/project/data/Detail.vue"),
                  },
                  {
                    path: "start_tuning_with",
                    name: "start-tuning-with-data",
                    component: () =>
                      import("../views/project/data/StartTuningWithData.vue"),
                  },
                ],
              },
              {
                path: "",
                name: "data-list",
                component: () => import("../views/project/data/List.vue"),
              },
            ],
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
