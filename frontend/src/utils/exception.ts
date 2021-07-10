import { ExceptionModule } from "@/store/exception";
import { AxiosError } from "axios";

export function alertAxiosError(error: AxiosError): void {
  ExceptionModule.setAxiosError(error);
}
export function resetAxiosError(): void {
  ExceptionModule.resetAxiosError();
}
