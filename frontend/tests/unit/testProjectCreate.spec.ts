import ProjectCreate from "@/views/ProjectList.vue";
import { createLocalVue, mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import Vuetify from "vuetify";
import * as RequestModule from "@/utils/request";
import { sleep } from "@/utils";
import router from "@/router";
import VueRouter from "vue-router";
import axios from "axios";
import { paths } from "@/api/schema";
import { AuthModule } from "@/store/auth";
type ProjectCreation =
  paths["/api/project/"]["post"]["responses"]["201"]["content"]["application/json"];

describe("ProjectList.vue", () => {
  const localVue = createLocalVue();
  localVue.use(VueRouter);
  let url: string;

  AuthModule.setToken("some token");

  const postSpy = jest
    .spyOn(axios, "post")
    .mockImplementationOnce((url_, data, config) => {
      url = url_;
      return new Promise((resolve) => {
        resolve({
          status: 201,
          data: { id: 42 },
          config: {},
          headers: {},
          statusText: "created",
        });
      });
    });

  const getSpy = jest.spyOn(axios, "get").mockImplementation((url) => {
    return new Promise((resolve) => {
      resolve({
        status: 200,
        data: url.match(/existing/g) ? [{ id: 41 }] : [],
        config: {},
        headers: {},
        statusText: "",
      });
    });
  });
  let vuetify: Vuetify;
  beforeEach(() => {
    vuetify = new Vuetify();
  });

  it("renders the form", async () => {
    const wrapper = mount(ProjectCreate, {
      localVue,
      vuetify,
      router,
      data() {
        return {
          tab: 0,
          projects: [],
        };
      },
    });
    router.push({ name: "project-list" });

    expect(wrapper.text()).toContain("No projects yet.");
    wrapper.find("[tab-project-create]").trigger("click");
    await flushPromises();

    wrapper.find('input[name="project-name"]').setValue("existing");
    wrapper.find('input[name="user column name"]').setValue("userID");
    wrapper.find('input[name="item column name"]').setValue("");

    await sleep(500);

    await flushPromises();

    expect(wrapper.text()).toContain("Project with this name already exists.");
    expect(wrapper.text()).not.toContain("user column name required.");
    expect(wrapper.text()).toContain("item column name required.");

    const btn = wrapper.find(".v-btn");
    btn.trigger("click");
    await flushPromises();

    expect(wrapper.vm.$route.name).toBe("project-list");

    expect(wrapper.text()).toContain("Project with this name already exists.");
    expect(wrapper.text()).not.toContain("user column name required.");
    expect(wrapper.text()).toContain("item column name required.");

    wrapper.find('input[name="project-name"]').setValue("project");
    wrapper.find('input[name="item column name"]').setValue("itemID");
    await sleep(500);

    await flushPromises();

    expect(wrapper.text()).not.toContain(
      "Project with this name already exists."
    );
    expect(wrapper.text()).not.toContain("user column name required.");
    expect(wrapper.text()).not.toContain("item column name required.");

    btn.trigger("click");
    await flushPromises();

    expect(url).toBe("/api/project/");

    expect(wrapper.vm.$route.name).toBe("project");
    expect(wrapper.vm.$route.params.projectId).toBe("42");
  });
});
