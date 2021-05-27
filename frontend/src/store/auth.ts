import {
  Module,
  VuexModule,
  MutationAction,
  getModule,
  //  Action,
  Mutation,
} from "vuex-module-decorators";
import store from "@/store";
import { TokenApiFactory, TokenObtainPair } from "@/api/client";
import Axios from "axios";
import { baseURL } from "@/env";

function retrieveToken(): string | null {
  return localStorage.getItem("recotem_token");
}

interface refreshTokenResult {
  access: string;
}

@Module({
  dynamic: true,
  store,
  name: "auth",
})
export class Auth extends VuexModule {
  token = "";
  refresh = "";
  isLoggedIn = false;
  logInError = false;

  @Mutation
  setToken(token: string) {
    this.token = token;
  }

  @MutationAction
  async login(payload: { username: string; password: string }) {
    const p: TokenObtainPair = {
      username: payload.username,
      password: payload.password,
      access: "",
      refresh: "",
    };
    try {
      const response = await TokenApiFactory(undefined, "").tokenCreate(p);
      return {
        token: response.data.access,
        refresh: response.data.refresh,
        isLoggedIn: true,
      };
    } catch (err) {
      alert(err);
    }
  }
  @MutationAction
  async refreshToken() {
    try {
      const refresh_token = (this.state as any).refresh as string;
      const refreshed = await Axios.post<refreshTokenResult>(
        `${baseURL}/token/refresh/`,
        {
          refresh: refresh_token,
        }
      ).catch((error) => {
        return null;
      });
      if (refreshed === null) {
        return {
          token: "",
          isLoggedIn: false,
        };
      } else {
        return {
          token: refreshed.data.access,
          isLoggedIn: true,
        };
      }
    } catch (err) {
      this.context.dispatch("logout");
    }
  }

  @MutationAction
  async logout() {
    return {
      token: "",
      isLoggedIn: true,
    };
  }
}

export const AuthModule = getModule(Auth);
