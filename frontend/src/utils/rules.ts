import {
  max_value as max_value_default,
  min_value as min_value_default,
} from "vee-validate/dist/rules";
export const min_value = {
  ...min_value_default,
  message: "The value must not be negative.",
};

export const min_value_1 = {
  ...min_value_default,
  message: "The value must be positive.",
};

export const max_value = {
  ...max_value_default,
  message: "The value must not exceed 1.0.",
};
export const is_integral = {
  validate(value: string | undefined | null | number) {
    if (value === undefined || value === null) return true;
    if (typeof value === "number") {
      return Math.floor(value) - value === 0.0;
    }
    if (parseInt(value) - parseFloat(value) === 0.0) {
      return true;
    } else return false;
  },
  message: "The value must be an integer.",
};
