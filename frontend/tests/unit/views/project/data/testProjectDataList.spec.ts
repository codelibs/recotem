import { mount, createLocalVue } from "@vue/test-utils";
import axios from "axios";
import MockAdapter from "axios-mock-adapter";
import { paths } from "@/api/schema";
import ProjectDataList from "@/views/project/data/List.vue";
import TrainingDataList from "@/components/TrainingDataList.vue";

import DataUploadDialogComponent from "@/components/DataUpload.vue";
import FileUpload from "@/components/FileUpload.vue";
import { AuthModule } from "@/store/auth";
import { MockFile } from "../../../../mockFile";
import Vuetify from "vuetify";
import VueRouter from "vue-router";
import flushPromises from "flush-promises";
import { sleep } from "@/utils";

const mock = new MockAdapter(axios);
const createdDataId = 42;
type CreatedData =
  paths["/api/training_data/"]["post"]["responses"]["201"]["content"]["application/json"];
const trainingDataURL = "/api/training_data/";
type PaginatedDataList =
  paths["/api/training_data/"]["get"]["responses"]["200"]["content"]["application/json"];

mock.onPost("/api/auth/token/refresh/").reply(async () => {
  return [
    200,
    {
      access_token: "some token",
    },
  ];
});

mock.onPost(trainingDataURL).reply(async (config) => {
  return [
    201,
    {
      id: createdDataId,
      project: 1,
      ins_datetime: "2021-02-01T00:00:00",
      basename: "hoge.csv",
    } as CreatedData,
  ];
});
mock.onGet(/^\/api\/training_data.*$/g).reply(async (config) => {
  return [
    200,
    {
      results: [
        {
          id: 4,
          project: 1,
          ins_datetime: "2021-02-01T00:00:00",
          basename: "hoge1.csv",
          filesize: 17, // 17.0B
        },
        {
          id: 8,
          project: 1,
          ins_datetime: "2021-02-02T00:00:00",
          basename: "hoge2.csv",
          filesize: null, // unknown
        },
      ],
      count: 2,
      next: null,
      previous: null,
    } as PaginatedDataList,
  ];
});
mock.onGet(/\/api\/item_meta_data\/.*$/).reply(async (config) => {
  return [200, []];
});

describe("List.vue", () => {
  document.body.setAttribute("data-app", "true");

  it("Retrieve & Upload file", async () => {
    window.IntersectionObserver = jest.fn().mockImplementation(() => ({
      observe: () => jest.fn(),
      unobserve: () => jest.fn(),
    }));

    const localVue = createLocalVue();
    localVue.use(Vuetify);
    AuthModule.setProjectId(1);

    const vuetify = new Vuetify();
    const router = new VueRouter({
      routes: [
        { name: "current", path: "" },
        { name: "start-tuning-with-data", path: "/start-tuning" },
        { name: "data-detail", path: "/data" },
      ],
    });

    const wrapper = mount(ProjectDataList, {
      localVue,
      router,
      vuetify,
      stubs: ["v-progress-linear"],
    });
    const dataListComponent = wrapper.findComponent(TrainingDataList);
    await flushPromises();
    expect(dataListComponent.text()).toContain("17B");
    expect(dataListComponent.text()).toContain("unknown");

    await dataListComponent.find("tbody tr").trigger("click");
    await flushPromises();
    expect(wrapper.vm.$route.name).toBe("data-detail");
  });
});
