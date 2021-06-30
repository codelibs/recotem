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
        redirect: () => {
          const projectId = parseInt(
            window.localStorage.getItem("projectId") || ""
          );
          if (!isNaN(projectId)) {
            return { name: "project", params: { projectId: `${projectId}` } };
          } else {
            return { name: "project-list" };
          }
        },
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
            path: "/first-tuning",
            name: "first-tuning",
            component: () =>
              import("../views/project/tuningjobs/StartTuning.vue"),
            props:{upload: true}
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
          {
            path: "tuning_job/",
            component: () => import("../views/project/Data.vue"),
            children: [
              {
                path: "start_tuning",
                name: "start-tuning",
                component: () =>
                  import("../views/project/tuningjobs/StartTuning.vue"),
              },
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
            path: "trained_model/",
            component: () => import("../views/project/Data.vue"),
            children: [
              {
                path: "",
                name: "trained-model-list",
                component: () => import("../views/project/models/List.vue"),
              },
              {
                path: "start_training",
                name: "start-training",
                component: () =>
                  import("../views/project/models/StartTraining.vue"),
              },
              {
                path: ":trainedModelId/",
                name: "trained-model-detail",
                component: () => import("../views/project/models/ModelTop.vue"),
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
