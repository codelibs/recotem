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
import { paths } from "@/api/schema";

const tokenObtainUrl = "/api/token/";
type tokenRequest =
  paths["/api/token/"]["post"]["requestBody"]["content"]["application/json"];
type tokenReturn =
  paths["/api/token/"]["post"]["responses"]["200"]["content"]["application/json"];

interface refreshTokenResult {
  access: string;
}

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

  @Mutation
  setToken(token: string | null) {
    this.token = token;
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
    this.username = username;
    console.log(`username is ${username}`);
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
      .post<tokenReturn>(tokenObtainUrl, p)
      .catch((error: AxiosError) => {
        const detail = error.response?.data?.detail;
        if (typeof detail === "string") {
          this.setLoginErrorMessage([detail]);
        }
        return null;
      });
    if (response !== null) {
      this.setToken(response.data.access);
      this.setRefresh(response.data.refresh);
      localStorage.setItem("refresh", response.data.refresh);
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
      .get<{ username: string }>(`${baseURL}/getme/`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      .catch((error: AxiosError) => {
        return null;
      });
    if (result !== null) {
      this.setUsername(result.data.username);
    }
  }

  @Action
  async logout() {
    this.setToken(null);
    this.setRefresh(null);
  }
}

export const AuthModule = getModule(Auth);
