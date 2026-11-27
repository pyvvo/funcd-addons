// The JavaScript lint of `just lint`: funcd's rule that an import is at the top of its module, never inside a function,
// where an import() would be.
export default [
  {
    ignores: ["work/", "dist/", ".cache/"],
  },
  {
    files: ["**/*.js", "**/*.mjs"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: ":function ImportExpression",
          message: "import at the top of the module, never inside a function",
        },
      ],
    },
  },
];
