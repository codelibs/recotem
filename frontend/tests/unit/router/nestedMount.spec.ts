import { shallowMount, mount, createLocalVue } from "@vue/test-utils";
import flushPromises from "flush-promises";
import Main from "@/views/Main.vue";
import VueRouter from "vue-router";
import Vuetify from "vuetify";

import router from "@/router";
import { AuthModule } from "@/store/auth";
import Dashboard from "@/views/project/Dashboard.vue";
import axios from "axios";
import { paths } from "@/api/schema";
const localVue = createLocalVue();
localVue.use(VueRouter);
localVue.use(Vuetify);

type DashboardData =
  paths["/api/project_summary/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];
const summaryURL = "/api/project_summary";

type ProjectDetail =
  paths["/api/project/{id}/"]["get"]["responses"]["200"]["content"]["application/json"];
const projectDetailURL = "/api/project";

describe("App", () => {
  let urls: string[] = [];
  const projectDetail = {};

  const getSpy = jest.spyOn(axios, "get").mockImplementation((url_: string) => {
    urls.push(url_);
    let data: DashboardData | ProjectDetail;
    if (url_.match(/\/api\/project\/\d+\//g)) {
      data = {
        id: 42,
        n_data: 0,
        n_complete_jobs: 0,
        n_models: 0,
        ins_datetime: "2019-01-01T00:00:00",
      };
    } else {
      data = {
        id: 1,
        ins_datetime: "2021-07-01T00:00:00",
        name: "hoge",
        user_column: "userId",
        item_column: "itemId",
      };
    }
    return new Promise((resolve) => {
      resolve({
        status: 200,
        statusText: "",
        headers: {},
        config: {},
        data,
      });
    });
  });

  it("renders a child component via routing", async () => {
    AuthModule.setToken("dummyToken");
    const vuetify = new Vuetify();

    const wrapper = mount(Main, {
      localVue,
      router,
      vuetify,
    });

    router.push({ name: "project", params: { projectId: "42" } });
    await flushPromises();
    //expect(urls0]).toBe(`${summaryURL}/42`);
    expect(urls.length).toBe(2);

    expect(wrapper.findComponent(Dashboard).exists()).toBe(true);
    expect(wrapper.text()).toContain("Start upload");
  });
});
