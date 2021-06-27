import moment from "moment";

export function prettifyDate(x: string) {
  return moment(x).format("YYYY-MM-DDTHH:mm:ss");
}
