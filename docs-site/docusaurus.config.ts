import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";
import { themes as prismThemes } from "prism-react-renderer";

// Treat empty strings as "unset" — GitHub Actions exposes undefined repo
// Variables as empty strings, and Docusaurus rejects empty url/baseUrl.
function nonEmpty(value: string | undefined): string | undefined {
  return value && value.trim().length > 0 ? value : undefined;
}

// Resolve repo / org / base URL from CI-provided env vars when present.
// Locally we fall back to a placeholder slug so the build can finish
// without a known GitHub repository — operators on a fork should set
// `GITHUB_REPOSITORY` (CI does this automatically) or override the
// `DOCUSAURUS_URL` / `DOCUSAURUS_BASE_URL` repo Variables.
const PLACEHOLDER_SLUG = "OWNER/graph-mcp";
const repoSlug = nonEmpty(process.env.GITHUB_REPOSITORY) ?? PLACEHOLDER_SLUG;
const [organizationName, projectName] = repoSlug.split("/");
const isOrgSite = projectName.endsWith(".github.io");

const configuredUrl = nonEmpty(process.env.DOCUSAURUS_URL);
const configuredBaseUrl = nonEmpty(process.env.DOCUSAURUS_BASE_URL);

const url = configuredUrl ?? `https://${organizationName}.github.io`;
const baseUrl = configuredBaseUrl ?? (isOrgSite ? "/" : `/${projectName}/`);

const config: Config = {
  title: "graph-mcp",
  tagline:
    "A safety-first MCP server: an LLM plans, the server validates, compiles, and executes.",
  favicon: "img/favicon.svg",
  url,
  baseUrl,

  // GitHub Pages deployment metadata. The CI workflow uses
  // actions/deploy-pages and does not call docusaurus deploy directly,
  // but these are still consumed by the edit-this-page links and the
  // theme.
  organizationName,
  projectName,
  deploymentBranch: "gh-pages",

  // We want broken links to fail the build so docs cannot rot
  // unnoticed.
  onBrokenLinks: "throw",
  onDuplicateRoutes: "throw",

  // Trailing slashes are explicit so URLs round-trip cleanly through
  // GitHub Pages (which serves /foo/index.html for /foo/).
  trailingSlash: true,

  i18n: {
    defaultLocale: "en",
    locales: ["en"],
  },

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: "throw",
    },
  },
  themes: [
    "@docusaurus/theme-mermaid",
    [
      // Local search keeps the docs site self-contained — no Algolia
      // dependency, no network calls during build or serve.
      "@easyops-cn/docusaurus-search-local",
      {
        hashed: true,
        indexDocs: true,
        indexBlog: false,
        docsRouteBasePath: "/",
        highlightSearchTermsOnTargetPage: true,
        explicitSearchResultPath: true,
      },
    ],
  ],

  presets: [
    [
      "classic",
      {
        docs: {
          path: "docs",
          routeBasePath: "/",
          sidebarPath: "./sidebars.ts",
          editUrl: `https://github.com/${organizationName}/${projectName}/tree/main/docs-site/`,
          showLastUpdateAuthor: false,
          showLastUpdateTime: true,
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      defaultMode: "light",
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "graph-mcp",
      logo: {
        alt: "graph-mcp",
        src: "img/logo.svg",
      },
      items: [
        {
          to: "/users/intro/",
          label: "User Guide",
          position: "left",
          activeBaseRegex: "^/users/",
        },
        {
          to: "/developers/architecture/",
          label: "Developer Guide",
          position: "left",
          activeBaseRegex: "^/developers/",
        },
        {
          to: "/reference/configuration-reference/",
          label: "Reference",
          position: "left",
          activeBaseRegex: "^/reference/",
        },
        {
          to: "/adr/0001-query-plan-ir-not-raw-sparql/",
          label: "ADRs",
          position: "left",
          activeBaseRegex: "^/adr/",
        },
        {
          href: `https://github.com/${organizationName}/${projectName}`,
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "User guide", to: "/users/intro/" },
            { label: "Developer guide", to: "/developers/architecture/" },
            { label: "Reference", to: "/reference/configuration-reference/" },
            { label: "Production readiness", to: "/developers/production-readiness/" },
          ],
        },
        {
          title: "Project",
          items: [
            {
              label: "GitHub",
              href: `https://github.com/${organizationName}/${projectName}`,
            },
            {
              label: "Issues",
              href: `https://github.com/${organizationName}/${projectName}/issues`,
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} graph-mcp authors. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ["bash", "json", "yaml", "python", "turtle"],
    },
    mermaid: {
      theme: { light: "neutral", dark: "dark" },
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
