import flushPromises from "flush-promises";

import Login from "@/views/Login.vue";
import { AuthModule } from "@/store/auth";
import axios, { AxiosPromise, AxiosError } from "axios";
import { mount, shallowMount, createLocalVue } from "@vue/test-utils";
import Vuetify from "vuetify";
import VueRouter from "vue-router";

describe("Login.vue", () => {
  const localVue = createLocalVue();
  localVue.use(VueRouter);

  let vuetify: Vuetify;
  let router: VueRouter;
  beforeEach(() => {
    vuetify = new Vuetify();
    router = new VueRouter({
      routes: [
        { name: "project-list", path: "/project-list" },
        { name: "login", path: "/login" },
      ],
    });
  });

  let url = "";
  const postSpy = jest
    .spyOn(axios, "post")
    .mockImplementation(
      (url_: string, data: { username: string; password: string }) => {
        url = url_;
        return new Promise((resolve, reject) => {
          if (data.password === "correct") {
            resolve({
              status: 200,
              statusText: "authorized",
              headers: {},
              config: {},
              data: { access_token: "returned token" },
            });
          } else {
            reject({
              response: {
                status: 400,
                data: {
                  non_field_errors: ["password not correct."],
                },
              },
            });
          }
        });
      }
    );
  AuthModule.setToken(null);

  it("normal response", async () => {
    const wrapper = mount(Login, {
      localVue,
      vuetify,
      router,
    });
    const userNameInput = wrapper.find('input[name="username"]');
    const passwordInput = wrapper.find('input[name="password"]');
    userNameInput.setValue("username");
    passwordInput.setValue("incorrect");
    const btn = wrapper.find('button[name="login"]');
    await flushPromises();

    expect((userNameInput.element as HTMLInputElement).value).toBe("username");

    btn.trigger("click");
    await flushPromises();
    expect((userNameInput.element as HTMLInputElement).value).toBe("");
    expect((passwordInput.element as HTMLInputElement).value).toBe("");

    expect(url).toBe("/api/auth/login/");
    expect(wrapper.text()).toContain("password not correct");
    expect(wrapper.vm.$data.password === "");

    userNameInput.setValue("username");
    passwordInput.setValue("correct");

    btn.trigger("click");
    await flushPromises();
    expect(AuthModule.token).toBe("returned token");
  });
});
