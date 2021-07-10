import { ExceptionModule } from "@/store/exception";
import { AxiosError } from "axios";

export function alertAxiosError(error: AxiosError): void {
  console.log(error);
  ExceptionModule.setAxiosError(error);
}
