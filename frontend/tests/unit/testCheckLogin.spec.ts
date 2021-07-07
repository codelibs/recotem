import flushPromises from "flush-promises";

import { AuthModule } from "@/store/auth";
import { checkLogin } from "@/utils/request";
import axios, { AxiosPromise, AxiosError } from "axios";
import { guard } from "@/router/guard";
import { Route } from "vue-router";

describe("checkLogin", () => {
  let url = "";
  afterEach(() => {
    jest.clearAllMocks();
  });

  const postSpy = jest
    .spyOn(axios, "post")
    .mockImplementation((url_, data_, config_) => {
      return new Promise((resolve) => {
        console.log("mock called");
        url = url_;
        resolve({
          headers: {},
          config: {},
          status: 200,
          statusText: "authorized",
          data: { access: "resulting token" },
        });
      });
    });
  AuthModule.setToken(null);

  it("normal response", async () => {
    const loginSuccess = await checkLogin(AuthModule);
    expect(url).toBe("/api/auth/token/refresh/");
    expect(AuthModule.token).toBe("resulting token");
    expect(loginSuccess).toBe(true);
  });
});
