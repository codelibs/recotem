import { Auth } from "@/store/auth";
import Axios, { AxiosError } from "axios";
import { baseURL } from "@/env";
import { components } from "@/api/schema";

type TokenRefresh = components["schemas"]["TokenRefresh"];

export const authHeader = (token: string) => ({
  Authorization: `Bearer ${token}`,
});

export async function refreshToken(module: Auth) {
  module.setToken(null);
  try {
    const refresh_token = module.refresh;
    if (refresh_token !== null) {
      const refreshed = await Axios.post<TokenRefresh>(
        `${baseURL}/token/refresh/`,
        {
          refresh: refresh_token,
        }
      ).catch((error) => {
        return null;
      });

      if (refreshed === null) {
        module.setRefresh(null);
      } else {
        module.setToken(refreshed.data.access);
      }
    } else {
      module.setRefresh(null);
    }
  } catch (err) {
    module.setRefresh(null);
  }
}

export async function checkLogin(module: Auth): Promise<boolean> {
  if (module.token !== null) {
    return true;
  }

  let refresh_token = module.refresh;
  if (!refresh_token) {
    refresh_token = localStorage.getItem("refresh");
    module.setRefresh(refresh_token);
  }

  if (!refresh_token) {
    return false;
  }
  await refreshToken(module);

  if (module.refresh === null) {
    return false;
  } else {
    await module.getUserName();
    return true;
  }
}

export async function getWithRefreshToken<Return>(
  module: Auth,
  path: string
): Promise<Return | null> {
  if (module.token === null) {
    return null;
  }
  const result = await Axios.get<Return>(path, {
    headers: authHeader(module.token),
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 401) {
      try {
        await refreshToken(module);
        const result = await Axios.get<Return>(path, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
    } else {
      throw error;
    }
  });
  if (result === null) {
    module.logout();
    return null;
  } else {
    return result.data;
  }
}

export async function postWithRefreshToken<Payload, Return>(
  module: Auth,
  path: string,
  payload: Payload
): Promise<Return | null> {
  if (module.token === null) {
    return null;
  }

  const result = await Axios.post<Return>(path, payload, {
    headers: { Authorization: `Bearer ${module.token}` },
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 401) {
      console.log("token expired?");
      console.log(error.response.data);
      await refreshToken(module);
      const result = await Axios.post<Return>(path, payload, {
        headers: {
          Authorization: `Bearer ${module.token}`,
        },
      });
      return result;
    } else {
      throw error;
    }
  });
  if (result === null) {
    module.logout();
    return null;
  } else {
    return result.data;
  }
}

export async function putWithRefreshToken<Payload, Return>(
  module: Auth,
  path: string,
  payload: Payload
): Promise<Return | null> {
  if (module.token === null) {
    return null;
  }

  const result = await Axios.put<Return>(path, payload, {
    headers: { Authorization: `Bearer ${module.token}` },
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 401) {
      try {
        await refreshToken(module);
        const result = await Axios.put<Return>(path, payload, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
    } else {
      throw error;
    }
    return null;
  });
  if (result === null) {
    module.logout();
    return null;
  } else {
    return result.data;
  }
}

export async function deleteWithRefreshToken(
  module: Auth,
  path: string
): Promise<void> {
  const result = await Axios.delete(path, {
    headers: { Authorization: `Bearer ${module.token}` },
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 401) {
      try {
        await refreshToken(module);
        const result = await Axios.delete(path, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
    } else {
      throw error;
    }
  });
  if (result === null) {
    module.logout();
    return;
  } else {
    return result.data;
  }
}

export function numberInputValueToNumberOrNull(
  value: number | undefined | null | string
): number | undefined {
  if (typeof value === "number" || value === undefined) {
    return value;
  } else {
    return undefined;
  }
}
