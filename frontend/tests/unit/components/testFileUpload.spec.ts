import flushPromises from "flush-promises";

import { AuthModule } from "@/store/auth";
import axios from "axios";
import { mount, createLocalVue } from "@vue/test-utils";
import { sleep } from "@/utils/index";
import Vuetify from "vuetify";
import VueRouter from "vue-router";
import MockAdapter from "axios-mock-adapter";
import FileUpload from "@/components/FileUpload.vue";
import { MockFile } from "../../mockFile";

const mock = new MockAdapter(axios);
const createdDataId = 103;

mock.onPost("/api/auth/token/refresh/").reply(async () => {
  return [
    200,
    {
      access_token: "some token",
    },
  ];
});

mock.onPost("/api/file-upload/").reply(async (config) => {
  const total = 1024; // mocked file size
  for (const progress of [0, 0.5, 1]) {
    await sleep(50);
    if (config.onUploadProgress) {
      config.onUploadProgress({ loaded: total * progress, total });
    }
  }
  return [200, { id: createdDataId }];
});

describe("FileUpload.vue", () => {
  const localVue = createLocalVue();
  localVue.use(VueRouter);
  const intersectionObserverMock = () => ({
    observe: () => null,
  });
  window.IntersectionObserver = jest
    .fn()
    .mockImplementation(intersectionObserverMock);
  let vuetify: Vuetify;
  beforeEach(() => {
    vuetify = new Vuetify();
  });

  it("normal response", async () => {
    const wrapper = mount(FileUpload, {
      localVue,
      vuetify,
      propsData: {
        postURL: "/api/file-upload/",
        fileLabel: "Upload dummy file",
      },
      stubs: ["v-progress-linear"],
    });
    await wrapper.setData({
      uploadProgress: null,
      uploadFile: MockFile("mockFileInput.csv", 1024, "application/csv"),
    });

    AuthModule.setProjectId(1);
    const uploadButton = wrapper.find("button[upload-button]");

    uploadButton.trigger("click");
    expect(wrapper.html()).toContain("mockFileInput.csv");
    await sleep(500);

    await flushPromises();
    const emittedInput = wrapper.emitted().input;
    expect(emittedInput).toBeDefined();
    if (emittedInput !== undefined) {
      expect(emittedInput.length).toBe(2);
      expect(emittedInput[1][0]).toBe(createdDataId);
    }
  });
});
