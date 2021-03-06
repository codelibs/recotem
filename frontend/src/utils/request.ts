import { Auth, AuthModule } from "@/store/auth";
import Axios, { AxiosPromise, AxiosError, AxiosRequestConfig } from "axios";
import { baseURL } from "@/env";
import { paths } from "@/api/schema";
import router from "@/router";
import Router from "vue-router";
import fileDownload from "js-file-download";
import { alertAxiosError } from "@/utils/exception";

const refreshURL = `${baseURL}/api/auth/token/refresh/`;
type RefreshResult =
  paths["/api/auth/token/refresh/"]["post"]["responses"]["200"]["content"]["application/json"];

const logoutUrl = "/api/auth/logout/";
type logoutResponse =
  paths["/api/auth/logout/"]["post"]["responses"]["200"]["content"]["application/json"];

const axiosCSRFConfg = {
  xsrfCookieName: "csrftoken",
  xsrfHeaderName: "X-CSRFTOKEN",
};
export const authHeader = (token: string): Record<string, string> => ({
  Authorization: `Bearer ${token}`,
});

export async function refreshToken(module: Auth): Promise<void> {
  module.setToken(null);
  const refreshed = await Axios.post<RefreshResult>(
    refreshURL,
    {},
    { ...axiosCSRFConfg }
  ).catch((exception: AxiosError) => {
    if (exception.response?.status !== 401) {
      alertAxiosError(exception);
    }
    return null;
  });
  if (refreshed === null) return;
  module.setToken(refreshed.data.access);
}

export async function checkLogin(module: Auth): Promise<boolean> {
  if (module.token !== null) {
    return true;
  }

  await refreshToken(module);
  return !(module.token === null);
}

interface AxiosMethod<ArgType, ReturnType> {
  (
    path: string,
    arg: ArgType,
    config: AxiosRequestConfig
  ): AxiosPromise<ReturnType>;
}

async function axiosMethodWithRetry<ArgType, ReturnType>(
  method: AxiosMethod<ArgType, ReturnType>,
  module: Auth,
  path: string,
  arg: ArgType,
  config: AxiosRequestConfig | undefined,
  alertError = true
): Promise<ReturnType> {
  if (module.token === null) {
    await refreshToken(module);
    if (module.token === null) {
      await logout(module, router);
      throw new Error("token is null after refresh.");
    }
  }
  const copiedConfig = { ...config };
  if (copiedConfig.headers === undefined) {
    copiedConfig["headers"] = new Object();
  }
  copiedConfig.headers = {
    ...copiedConfig.headers,
    ...authHeader(module.token),
  };
  copiedConfig.xsrfCookieName = "csrftoken";
  copiedConfig.xsrfHeaderName = "X-CSRFTOKEN";

  const result = await method(`${baseURL}${path}`, arg, copiedConfig).catch(
    async (error: AxiosError) => {
      if (error.response?.status === 401) {
        console.log("Try refreshing token...");
        await refreshToken(module);
        if (module.token === null) {
          throw new Error("token is null after refresh.");
        }
        copiedConfig.headers = {
          ...copiedConfig.headers,
          ...authHeader(module.token),
        };
        const result = await method(`${baseURL}${path}`, arg, copiedConfig);
        return result;
      } else {
        if (alertError) {
          alertAxiosError(error);
        }
        throw error;
      }
    }
  );
  return result.data;
}

export async function getWithRefreshToken<Return>(
  module: Auth,
  path: string,
  config: AxiosRequestConfig | undefined = undefined,
  alertError = true
): Promise<Return> {
  return axiosMethodWithRetry<undefined, Return>(
    async (
      path: string,
      _: undefined,
      config_: AxiosRequestConfig | undefined
    ) => Axios.get<Return>(path, config_),
    module,
    path,
    undefined,
    config,
    alertError
  );
}

export async function downloadWithRefreshToken(
  module: Auth,
  path: string,
  saveName: string
): Promise<boolean> {
  const response = await axiosMethodWithRetry<undefined, Blob>(
    async (
      path: string,
      _: undefined,
      config_: AxiosRequestConfig | undefined
    ) => Axios.get<any>(path, config_),
    module,
    path,
    undefined,
    { responseType: "blob" }
  );
  fileDownload(response, saveName);
  return true;
}

export async function postWithRefreshToken<Payload, Return>(
  module: Auth,
  path: string,
  payload: Payload,
  config: AxiosRequestConfig | undefined = undefined,
  alertError = true
): Promise<Return> {
  return axiosMethodWithRetry<Payload, Return>(
    async (
      path: string,
      payload: Payload,
      config_: AxiosRequestConfig | undefined
    ) => Axios.post<Return>(path, payload, config_),
    module,
    path,
    payload,
    config,
    alertError
  );
}

export async function deleteWithRefreshToken<Return>(
  module: Auth,
  path: string,
  config: AxiosRequestConfig | undefined
): Promise<Return> {
  return axiosMethodWithRetry<undefined, Return>(
    async (
      path: string,
      _: undefined,
      config_: AxiosRequestConfig | undefined
    ) => Axios.delete(path, config_),
    module,
    path,
    undefined,
    config
  );
}

export async function logout(module: Auth, router: Router): Promise<void> {
  if (module.token === null) {
    await refreshToken(module);
  }
  const logoutResult = await Axios.post<logoutResponse>(
    logoutUrl,
    {},
    axiosCSRFConfg
  );
  console.log(logoutResult.data.detail);
  AuthModule.setToken(null);
  router.push({ name: "login" });
}
