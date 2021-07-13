import {
  max_value as max_value_default,
  min_value as min_value_default,
} from "vee-validate/dist/rules";
import { ValidationRuleFunction } from "vee-validate/dist/types/types.d";

export const validRatio = {
  validate: ((v) =>
    min_value_default.validate(v, { min: 0.0 }) &&
    max_value_default.validate(v, { max: 1.0 })) as ValidationRuleFunction,
  message: "The value must be in the range [0.0, 1.0].",
};

export const isInteger = {
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

export const isPositiveInteger = {
  validate: ((v) =>
    min_value_default.validate(v, { min: 1.0 }) &&
    isInteger.validate(v)) as ValidationRuleFunction,
  message: "The value must be a positive integer.",
};
export const isNonnegativeInteger = {
  validate: ((v) =>
    min_value_default.validate(v, { min: 0.0 }) &&
    isInteger.validate(v)) as ValidationRuleFunction,
  message: "The value must be a non-negative integer.",
};
