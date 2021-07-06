import ProjectCreate from "@/components/ProjectCreate.vue";
import { createLocalVue, mount } from "@vue/test-utils";
import flushPromises from "flush-promises";
import Vuetify from "vuetify";
import * as RequestModule from "@/utils/request";
import { sleep } from "@/utils";
import VueRouter from "vue-router";

describe("ProjectCreate.vue", () => {
  const localVue = createLocalVue();
  localVue.use(VueRouter);

  const postSpy = jest
    .spyOn(RequestModule, "postWithRefreshToken")
    .mockResolvedValueOnce({ id: 42 });

  const getSpy = jest
    .spyOn(RequestModule, "getWithRefreshToken")
    .mockResolvedValueOnce([{ id: 41 }])
    .mockResolvedValueOnce([]);

  let vuetify: Vuetify;
  let router: VueRouter;
  beforeEach(() => {
    vuetify = new Vuetify();
    router = new VueRouter({
      routes: [
        {
          path: "/",
          name: "current",
        },
        {
          path: "/project",
          name: "project",
        },
      ],
    });
  });

  it("renders the form", async () => {
    const wrapper = mount(ProjectCreate, {
      localVue,
      vuetify,
      router,
    });
    const vm = wrapper.vm;

    wrapper.find('input[name="project-name"]').setValue("project");
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

    expect(wrapper.vm.$route.name).toBe("current");

    expect(wrapper.text()).toContain("Project with this name already exists.");
    expect(wrapper.text()).not.toContain("user column name required.");
    expect(wrapper.text()).toContain("item column name required.");

    wrapper.find('input[name="project-name"]').setValue("project2");
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

    expect(wrapper.vm.$route.name).toBe("project");
    expect(wrapper.vm.$route.params.projectId).toBe("42");
  });
});
