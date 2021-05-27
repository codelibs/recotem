import { Auth } from "@/store/auth";
import Axios, { AxiosError } from "axios";

export async function getWithRefreshToken<Return>(
  module: Auth,
  path: string
): Promise<Return | null> {
  const result = await Axios.get<Return>(path, {
    headers: { Authorization: `Bearer ${module.token}` },
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 403) {
      try {
        await module.refreshToken();
        const result = await Axios.get<Return>(path, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
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

export async function postWithRefreshToken<Payload, Return>(
  module: Auth,
  path: string,
  payload: Payload
): Promise<Return | null> {
  const result = await Axios.post<Return>(path, payload, {
    headers: { Authorization: `Bearer ${module.token}` },
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 403) {
      try {
        await module.refreshToken();
        const result = await Axios.post<Return>(path, payload, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
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

export async function putWithRefreshToken<Payload, Return>(
  module: Auth,
  path: string,
  payload: Payload
): Promise<Return | null> {
  const result = await Axios.put<Return>(path, payload, {
    headers: { Authorization: `Bearer ${module.token}` },
  }).catch(async (error: AxiosError) => {
    if (error.response?.status === 403) {
      try {
        await module.refreshToken();
        const result = await Axios.put<Return>(path, payload, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
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
    if (error.response?.status === 403) {
      try {
        await module.refreshToken();
        const result = await Axios.delete(path, {
          headers: {
            Authorization: `Bearer ${module.token}`,
          },
        });
        return result;
      } catch (e) {
        return null;
      }
    }
    return null;
  });
  if (result === null) {
    module.logout();
    return;
  } else {
    return result.data;
  }
}
