import { mount, createLocalVue } from "@vue/test-utils";
import axios from "axios";
import MockAdapter from "axios-mock-adapter";
import { paths } from "@/api/schema";
import StartTuning from "@/views/project/tuningjobs/StartTuning.vue";
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

const createdSplitConfigId = 10;
const splitConfigURL = "/api/split_config/";

const createdEvaluationConfigId = 100;
const evaluationConfigURL = "/api/evaluation_config/";

const parameterTuningJobURL = "/api/parameter_tuning_job/";
const createdJobId = "1000";

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
      project: 3,
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
          filesize: 17 * 1024, // 17.0kB
        },
        {
          id: 8,
          project: 1,
          ins_datetime: "2021-02-02T00:00:00",
          basename: "hoge2.csv",
          filesize: 12 * 1024 * 1024, // 12.0MB
        },
        {
          id: 16,
          project: 1,
          ins_datetime: "2021-02-03T00:00:00",
          basename: "hoge3.csv",
          filesize: 100 * 1024 * 1024 * 1024, // 10.0GB
        },
      ],

      count: 2,
      next: null,
      previous: null,
    } as PaginatedDataList,
  ];
});
mock.onGet(/^\/api\/split_config\/.*$/g).reply(async (config) => {
  return [200, []];
});
mock.onPost(splitConfigURL).reply(async () => {
  return [
    201,
    {
      id: createdSplitConfigId,
    },
  ];
});

mock.onGet(/^\/api\/evaluation_config\/.*$/g).reply(async (config) => {
  return [200, []];
});
mock.onPost(evaluationConfigURL).reply(async () => {
  return [
    201,
    {
      id: createdEvaluationConfigId,
    },
  ];
});

mock.onPost(parameterTuningJobURL).reply(async () => {
  return [
    201,
    {
      id: createdJobId,
    },
  ];
});

describe("StartTuning.vue", () => {
  it("Upload mode", async () => {
    const localVue = createLocalVue();
    localVue.use(Vuetify);

    const vuetify = new Vuetify();
    const router = new VueRouter({
      routes: [{ name: "tuning-job-detail", path: "/job" }],
    });

    const wrapper = mount(StartTuning, {
      propsData: {
        upload: true,
      },
      localVue,
      router,
      vuetify,
      stubs: ["v-progress-linear"],
    });
    const uploadComponent = wrapper.findComponent(FileUpload);
    expect(wrapper.html()).toContain("upload");
    await uploadComponent.setData({
      uploadProgress: null,
      uploadFile: MockFile("mockFileInput.csv", 1024, "application/csv"),
    });

    expect(wrapper.find(".v-stepper__step--active").text()).toContain("Upload");
    AuthModule.setProjectId(1);

    await wrapper.find("button[upload-button]").trigger("click");
    await flushPromises();

    expect(wrapper.find(".v-stepper__step--active").text()).toContain("Split");

    await wrapper
      .findAll("div.v-stepper__content")
      .wrappers[1].find("button.info")
      .trigger("click");
    expect(wrapper.find(".v-stepper__step--active").text()).toContain(
      "Evaluation"
    );

    await wrapper
      .findAll("div.v-stepper__content")
      .wrappers[2].find("button.info")
      .trigger("click");
    expect(wrapper.find(".v-stepper__step--active").text()).toContain(
      "Job Configuration"
    );
    await wrapper
      .findAll("div.v-stepper__content")
      .wrappers[3].find("button.primary")
      .trigger("click");
    await flushPromises();
    await sleep(200);

    expect(wrapper.vm.$route.name).toBe("tuning-job-detail");
    expect(wrapper.vm.$route.params.parameterTuningJobId).toBe(createdJobId);
  });

  it("Select mode", async () => {
    window.IntersectionObserver = jest.fn().mockImplementation(() => ({
      observe: () => jest.fn(),
      unobserve: () => jest.fn(),
    }));
    mock.resetHistory();
    const localVue = createLocalVue();
    localVue.use(Vuetify);

    const vuetify = new Vuetify();
    const router = new VueRouter({
      routes: [
        { name: "tuning-job-detail", path: "/job" },
        { name: "current", path: "" },
      ],
    });

    AuthModule.setProjectId(1);
    const wrapper = mount(StartTuning, {
      propsData: {
        upload: false,
      },
      localVue,
      router,
      vuetify,
      stubs: {
        "v-progress-linear": true,
      },
    });
    wrapper.vm.$router.push({ name: "current" });
    await flushPromises();

    expect(wrapper.find(".v-stepper__step--active").text()).toContain(
      "Select Data"
    );
    expect(wrapper.text()).toContain("hoge1.csv");
    expect(wrapper.text()).toContain("17.0kB");

    expect(wrapper.text()).toContain("hoge2.csv");
    expect(wrapper.text()).toContain("12.0MB");

    expect(wrapper.text()).toContain("hoge3.csv");
    expect(wrapper.text()).toContain("100.0GB");

    await flushPromises();

    // should not go to next step as we haven't selected the data
    const firstStepContent = wrapper.findAll("div.v-stepper__content")
      .wrappers[0];

    await firstStepContent.find("button").trigger("click");
    expect(wrapper.find(".v-stepper__step--active").text()).not.toContain(
      "Split"
    );

    await firstStepContent
      .findAll(".v-simple-checkbox i")
      .wrappers[0].trigger("click");
    await flushPromises();

    // can go to next
    await firstStepContent.find("button.info").trigger("click");

    expect(wrapper.find(".v-stepper__step--active").text()).toContain("Split");

    await wrapper
      .findAll("div.v-stepper__content")
      .wrappers[1].find("button.info")
      .trigger("click");
    expect(wrapper.find(".v-stepper__step--active").text()).toContain(
      "Evaluation"
    );

    await wrapper
      .findAll("div.v-stepper__content")
      .wrappers[2].find("button.info")
      .trigger("click");
    expect(wrapper.find(".v-stepper__step--active").text()).toContain(
      "Job Configuration"
    );
    await wrapper
      .findAll("div.v-stepper__content")
      .wrappers[3].find("button.primary")
      .trigger("click");
    await flushPromises();
    await sleep(200);

    expect(wrapper.vm.$route.name).toBe("tuning-job-detail");
    expect(wrapper.vm.$route.params.parameterTuningJobId).toBe(createdJobId);

    expect(mock.history.post[mock.history.post.length - 1].data).toContain(
      '"data":4'
    );
  });
});
