import {
  postWithRefreshToken,
  getWithRefreshToken,
  putWithRefreshToken,
  deleteWithRefreshToken,
  checkLogin,
} from "./request";

export {
  postWithRefreshToken,
  getWithRefreshToken,
  putWithRefreshToken,
  deleteWithRefreshToken,
  checkLogin,
};

export function sleep(msec: number): Promise<void> {
  return new Promise((resolve: any) => setTimeout(resolve, msec));
}
