import pluginVue from "eslint-plugin-vue";
import tsParser from "@typescript-eslint/parser";
import vueParser from "vue-eslint-parser";

export default [
  {
    ignores: ["dist/**", "node_modules/**"],
  },
  ...pluginVue.configs["flat/recommended"],
  {
    files: ["src/**/*.{ts,vue}"],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tsParser,
        sourceType: "module",
      },
    },
    rules: {
      "vue/multi-word-component-names": "off",
    },
  },
];
