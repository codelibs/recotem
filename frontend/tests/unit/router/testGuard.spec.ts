import { AuthModule } from "@/store/auth";
import axios from "axios";
import { guard } from "@/router/guard";
import { Route } from "vue-router";

describe("checkLogin", () => {
  const from: Route = {
    name: "fromPath",
    hash: "#",
    query: {},
    params: {},
    path: "/from",
    fullPath: "/from",
    matched: [],
  };
  const to: Route = {
    name: "toPath",
    hash: "#",
    query: {},
    params: {},
    path: "/to",
    fullPath: "/to",
    matched: [],
  };
  const login: Route = {
    name: "login",
    hash: "#",
    query: {},
    params: {},
    path: "/login",
    fullPath: "/login",
    matched: [],
  };

  it("guard already logged in", async () => {
    AuthModule.setToken("nice token");
    const mockNext = jest.fn();
    await guard(to, from, mockNext);
    expect(mockNext).toHaveBeenCalledWith();
  });
  const postSpy = jest
    .spyOn(axios, "post")
    .mockRejectedValueOnce({ response: { status_code: 401 } })
    .mockRejectedValueOnce({ response: { status_code: 401 } });

  it("should redirect to  log in", async () => {
    AuthModule.setToken(null);
    const mockNext = jest.fn();
    await guard(to, from, mockNext);
    console.log(mockNext.mock.calls);
    expect(mockNext).toHaveBeenCalledWith({
      name: "login",
      query: { redirect: to.fullPath },
    });
  });

  it("can freely go to login", async () => {
    AuthModule.setToken(null);
    const mockNext = jest.fn();
    await guard(login, from, mockNext);
    expect(mockNext).toHaveBeenCalledWith({
      name: "login",
      query: { redirect: login.fullPath },
    });
  });
});
