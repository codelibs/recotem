import Main from "@/views/Main.vue";
import { alertAxiosError, resetAxiosError } from "@/utils/exception";
import { mount, createLocalVue } from "@vue/test-utils";
import Vuetify from "vuetify";
import VueRouter from "vue-router";
import flushPromises from "flush-promises";

describe("Main.vue", () => {
  const localVue = createLocalVue();

  let vuetify: Vuetify;
  let router: VueRouter;
  beforeEach(() => {
    vuetify = new Vuetify();
    router = new VueRouter();
  });

  it("renders error correctly", async () => {
    const wrapper = mount(Main, {
      localVue,
      router,
      vuetify,
    });
    resetAxiosError();
    await flushPromises();
    expect(wrapper.text()).toBe("");
    alertAxiosError({
      config: {},
      message: "Unhandled exception without response",
      name: "",
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Unhandled exception without response");

    alertAxiosError({
      config: {},
      message: "",
      name: "",
      response: {
        status: 400,
        statusText: "A bad request example",
        data: "dummyDataText",
        config: {},
        headers: {},
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("dummyDataText");

    wrapper.find("[ignore-error]").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toBe("");
  });
});
