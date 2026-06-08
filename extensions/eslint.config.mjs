import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["dist/**", "node_modules/**"] },

  {
    files: ["browser-ext/**/*.js", "shared/**/*.js", "sites/**/*.js"],
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        ...globals.webextensions,
        L: "readonly",
      },
    },
  },

  {
    files: ["userscripts/**/*.js"],
    ...js.configs.recommended,
    rules: {
      ...js.configs.recommended.rules,
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        GM_getValue: "readonly",
        GM_setValue: "readonly",
        GM_addStyle: "readonly",
        GM_getResourceText: "readonly",
        GM_xmlhttpRequest: "readonly",
        GM_registerMenuCommand: "readonly",
        L: "readonly",
      },
    },
  },
];
