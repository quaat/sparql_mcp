import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

const sidebars: SidebarsConfig = {
  users: [
    {
      type: "category",
      label: "User guide",
      collapsed: false,
      link: { type: "doc", id: "users/intro" },
      items: [
        "users/intro",
        "users/quickstart",
        "users/installation",
        "users/configuration",
        "users/running-the-server",
        "users/connecting-clients",
        "users/mcp-tools",
        "users/mcp-resources",
        "users/query-plan-basics",
        "users/schema-discovery",
        "users/raw-sparql-mode",
        "users/security-and-deployment",
        "users/troubleshooting",
        "users/faq",
      ],
    },
  ],
  developers: [
    {
      type: "category",
      label: "Developer guide",
      collapsed: false,
      link: { type: "doc", id: "developers/architecture" },
      items: [
        "developers/architecture",
        "developers/repository-structure",
        "developers/query-plan-ir",
        "developers/validator",
        "developers/renderer",
        "developers/endpoints",
        "developers/schema-provider",
        "developers/term-resolution",
        "developers/mcp-integration",
        "developers/evals",
        "developers/testing",
        "developers/ci",
        "developers/extension-guide",
        "developers/production-readiness",
        "developers/release-process",
      ],
    },
  ],
  reference: [
    {
      type: "category",
      label: "Reference",
      collapsed: false,
      link: { type: "doc", id: "reference/configuration-reference" },
      items: [
        "reference/configuration-reference",
        "reference/tools-reference",
        "reference/resources-reference",
        "reference/query-plan-schema",
        "reference/validation-errors",
        "reference/security-policy",
        "reference/eval-metrics",
      ],
    },
  ],
  adr: [
    {
      type: "category",
      label: "Architecture decisions",
      collapsed: false,
      items: [
        "adr/0001-query-plan-ir-not-raw-sparql",
        "adr/0002-deterministic-validator-and-renderer",
        "adr/0003-raw-sparql-disabled-by-default",
        "adr/0004-docusaurus-documentation-site",
      ],
    },
  ],
};

export default sidebars;
