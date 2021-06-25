import {
  Module,
  VuexModule,
  getModule,
  Action,
  Mutation,
} from "vuex-module-decorators";
import store from "@/store";
import axios, { AxiosError } from "axios";
import { baseURL } from "@/env";
import { paths, components } from "@/api/schema";

const tokenObtainUrl = "/api/auth/login/";
type tokenRequest =
  paths["/api/auth/login/"]["post"]["requestBody"]["content"]["application/json"];
type tokenReturn =
  paths["/api/auth/login/"]["post"]["responses"]["200"]["content"]["application/json"];

const getMeUrl = "/api/auth/user/";
type getMeResponse =
  paths["/api/auth/user/"]["get"]["responses"]["200"]["content"]["application/json"];

type Project = components["schemas"]["Project"];

@Module({
  dynamic: true,
  store,
  name: "auth",
})
export class Auth extends VuexModule {
  token: string | null = null;
  refresh: string | null = null;
  loginErrorMessages: string[] = [];
  username: string | null = null;
  currentProjectId: number | null = null;
  currentProjectDetail: Project | null = null;

  @Mutation
  setToken(token: string | null) {
    this.token = token;
  }

  @Mutation
  setProjectId(id: number) {
    this.currentProjectId = id;
  }

  @Mutation
  setProjectDetail(project: Project) {
    this.currentProjectDetail = project;
  }

  @Mutation
  setLoginErrorMessage(vals: string[]) {
    this.loginErrorMessages = vals;
  }

  @Mutation
  setRefresh(refresh: string | null) {
    this.refresh = refresh;
  }

  @Mutation
  setUsername(username: string | null) {
    console.log("username", username);
    this.username = username;
  }

  @Action
  async login(payload: { username: string; password: string }) {
    const p = {
      username: payload.username,
      password: payload.password,
      access: "",
      refresh: "",
    } as tokenRequest;

    const response = await axios
      .post<tokenReturn>(tokenObtainUrl, p, {
        xsrfCookieName: "csrftoken",
        xsrfHeaderName: "X-CSRFTOKEN",
      })
      .catch((error: AxiosError) => {
        const detail = error.response?.data?.detail;
        if (typeof detail === "string") {
          this.setLoginErrorMessage([detail]);
        }
        return null;
      });
    if (response !== null) {
      this.setToken(response.data.access_token);
      await this.getUserName();
    } else {
      this.setToken(null);
      this.setRefresh(null);
    }
  }

  @Action
  async getUserName() {
    const token = this.token as string | null;
    const result = await axios
      .get<getMeResponse>(`${baseURL}${getMeUrl}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      .catch((error: AxiosError) => {
        return null;
      });

    if (result !== null) {
      this.setUsername(result.data.username);
    }
  }
}

export const AuthModule = getModule(Auth);
