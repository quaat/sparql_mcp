import type { ReactElement } from "react";
import Link from "@docusaurus/Link";
import Layout from "@theme/Layout";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";

export default function Home(): ReactElement {
  const { siteConfig } = useDocusaurusContext();
  return (
    <Layout
      title={siteConfig.title}
      description="A safety-first MCP server for SPARQL via a strict QueryPlan IR."
    >
      <header style={{ padding: "4rem 2rem 2rem", textAlign: "center" }}>
        <h1 style={{ fontSize: "2.75rem", marginBottom: "0.5rem" }}>
          graph-mcp
        </h1>
        <p style={{ fontSize: "1.25rem", maxWidth: 720, margin: "0 auto" }}>
          The LLM plans. The MCP server validates, compiles, executes, and
          explains.
        </p>
        <p style={{ marginTop: "1.5rem" }}>
          <Link
            className="button button--primary button--lg"
            to="/users/intro/"
            style={{ marginRight: "0.75rem" }}
          >
            User guide
          </Link>
          <Link
            className="button button--secondary button--lg"
            to="/developers/architecture/"
          >
            Developer guide
          </Link>
        </p>
      </header>

      <main style={{ padding: "0 2rem 4rem" }}>
        <section
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: "1.5rem",
            maxWidth: 1080,
            margin: "0 auto",
          }}
        >
          <FeatureCard
            title="Strict IR, not raw SPARQL"
            href="/developers/query-plan-ir/"
          >
            QueryPlan is a Pydantic-validated IR. The renderer escapes
            deterministically; the validator enforces scope, allowlists, and
            limits.
          </FeatureCard>
          <FeatureCard
            title="Read-only by default"
            href="/users/security-and-deployment/"
          >
            Update, DESCRIBE, and unallowlisted SERVICE are rejected. Raw
            SPARQL mode is opt-in and gated by a token-aware scanner.
          </FeatureCard>
          <FeatureCard
            title="Schema-aware tools"
            href="/users/mcp-tools/"
          >
            <code>resolve_terms</code>, <code>validate_query_plan</code>,
            <code> render_sparql</code>, <code>query_graph</code>, and
            <code> explain_query_plan</code> over a discovered schema.
          </FeatureCard>
          <FeatureCard
            title="Operate with confidence"
            href="/developers/production-readiness/"
          >
            Threat model, deployment settings, allowlist guidance, and known
            non-goals for operators planning a deployment.
          </FeatureCard>
        </section>
      </main>
    </Layout>
  );
}

function FeatureCard({
  title,
  href,
  children,
}: {
  title: string;
  href: string;
  children: React.ReactNode;
}): ReactElement {
  return (
    <Link
      to={href}
      style={{
        display: "block",
        padding: "1.5rem",
        border: "1px solid var(--ifm-color-emphasis-300)",
        borderRadius: 8,
        textDecoration: "none",
        color: "inherit",
        backgroundColor: "var(--ifm-background-surface-color)",
      }}
    >
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p style={{ margin: 0 }}>{children}</p>
    </Link>
  );
}
