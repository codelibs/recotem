import {
  Module,
  VuexModule,
  getModule,
  Mutation,
} from "vuex-module-decorators";
import store from "@/store";

import { AxiosError } from "axios";

@Module({
  dynamic: true,
  store,
  name: "exception",
})
export class Exception extends VuexModule {
  axiosError: AxiosError | null = null;

  @Mutation
  setAxiosError(error: AxiosError) {
    this.axiosError = error;
  }

  @Mutation
  resetAxiosError() {
    this.axiosError = null;
  }
}

export const ExceptionModule = getModule(Exception);
