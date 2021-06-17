import { Auth } from "@/store/auth";
import Axios, { AxiosPromise, AxiosError, AxiosRequestConfig } from "axios";
import { baseURL } from "@/env";
import { paths } from "@/api/schema";
import Router from "vue-router";

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
  ).catch(() => {
    return null;
  });
  if (refreshed !== null) {
    console.log("Refreshed token");
    module.setToken(refreshed.data.access);
  }
}

export async function checkLogin(module: Auth): Promise<boolean> {
  if (module.token !== null) {
    return true;
  }

  await refreshToken(module);

  if (module.token === null) {
    return false;
  } else {
    await module.getUserName();
    return true;
  }
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
  config: AxiosRequestConfig | undefined
): Promise<ReturnType | null> {
  if (module.token === null) {
    return null;
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
          return null;
        }
        copiedConfig.headers = {
          ...copiedConfig.headers,
          ...authHeader(module.token),
        };
        const result = await method(`${baseURL}${path}`, arg, copiedConfig);
        return result;
      } else {
        throw error;
      }
    }
  );
  if (result === null) {
    return null;
  }
  return result.data;
}

export async function getWithRefreshToken<Return>(
  module: Auth,
  path: string,
  config: AxiosRequestConfig | undefined = undefined
): Promise<Return | null> {
  return axiosMethodWithRetry<undefined, Return>(
    async (
      path: string,
      _: undefined,
      config_: AxiosRequestConfig | undefined
    ) => Axios.get<Return>(path, config_),
    module,
    path,
    undefined,
    config
  );
}

export async function postWithRefreshToken<Payload, Return>(
  module: Auth,
  path: string,
  payload: Payload,
  config: AxiosRequestConfig | undefined = undefined
) {
  return axiosMethodWithRetry<Payload, Return>(
    async (
      path: string,
      payload: Payload,
      config_: AxiosRequestConfig | undefined
    ) => Axios.post<Return>(path, payload, config_),
    module,
    path,
    payload,
    config
  );
}

export async function putWithRefreshToken<Payload, Return>(
  module: Auth,
  path: string,
  payload: Payload,
  config: AxiosRequestConfig | undefined = undefined
): Promise<Return | null> {
  return axiosMethodWithRetry<Payload, Return>(
    async (
      path: string,
      payload: Payload,
      config_: AxiosRequestConfig | undefined
    ) => Axios.put<Return>(path, payload, config_),
    module,
    path,
    payload,
    config
  );
}

export async function deleteWithRefreshToken<Return>(
  module: Auth,
  path: string,
  config: AxiosRequestConfig | undefined
): Promise<Return | null> {
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
  const logoutResult = await postWithRefreshToken<
    Record<string, never>,
    logoutResponse
  >(module, logoutUrl, {});
  if (logoutResult !== null) {
    console.log(logoutResult.detail);
  }
  router.push({ name: "login" });
}
