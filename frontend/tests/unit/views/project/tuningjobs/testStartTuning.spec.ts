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
type DataReturn =
  paths["/api/training_data/"]["post"]["responses"]["201"]["content"]["application/json"];
const trainingDataURL = "/api/training_data/";

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
    } as DataReturn,
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
  it("step", async () => {
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
});
