import {
  Module,
  VuexModule,
  getModule,
  Mutation,
} from "vuex-module-decorators";
import store from "@/store";
import { recotemVersion, docURLBase } from "@/env";
import { paths, components } from "@/api/schema";

const tokenObtainUrl = "/api/auth/login/";
type tokenRequest =
  paths["/api/auth/login/"]["post"]["requestBody"]["content"]["application/json"];
type tokenReturn =
  paths["/api/auth/login/"]["post"]["responses"]["200"]["content"]["application/json"];

type Project = components["schemas"]["Project"];

@Module({
  dynamic: true,
  store,
  name: "auth",
})
export class Auth extends VuexModule {
  token: string | null = null;
  currentProjectId: number | null = null;
  currentProjectDetail: Project | null = null;
  recotemVersion: string = recotemVersion;
  docURLBase: string = docURLBase;
  errors: string[] = [];

  @Mutation
  setToken(token: string | null) {
    this.token = token;
  }

  @Mutation
  setProjectId(id: number) {
    this.currentProjectId = id;
    window.localStorage.setItem("projectId", `${id}`);
  }

  @Mutation
  setProjectDetail(project: Project) {
    this.currentProjectDetail = project;
  }

  @Mutation
  resetProject() {
    this.currentProjectId = null;
    this.currentProjectDetail = null;
    window.localStorage.removeItem("projectId");
  }
}

export const AuthModule = getModule(Auth);
