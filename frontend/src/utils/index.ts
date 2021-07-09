import {
  postWithRefreshToken,
  getWithRefreshToken,
  deleteWithRefreshToken,
  checkLogin,
} from "./request";

export {
  postWithRefreshToken,
  getWithRefreshToken,
  deleteWithRefreshToken,
  checkLogin,
};

export function sleep(msec: number): Promise<void> {
  return new Promise((resolve: any) => setTimeout(resolve, msec));
}
