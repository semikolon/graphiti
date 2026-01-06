from pydantic import BaseModel, Field


class ProgrammingLanguage(BaseModel):
    """A programming language, such as Python, Go, or Rust."""
    pass


class Project(BaseModel):
    """A software project, codebase, or development effort.

    Entity name should be the project name for display.
    Examples: 'dotfiles', 'brf-auto', 'kimonokittens', 'graphiti-mcp'
    """
    slug: str = Field(..., description="Unique project identifier for canonical identity")
    description: str = Field(..., description="A brief description of what the project does or its purpose.")


class Library(BaseModel):
    """A software library or package dependency.

    Entity name should be the canonical library name.
    Examples: 'jsonwebtoken', 'ioredis', 'express', 'pytest', 'pandas', 'react'
    """
    slug: str = Field(..., description="kebab-case stable slug for unique identification")
    language: str | None = Field(None, description="Primary programming language/ecosystem (e.g., 'TypeScript', 'Python', 'Go')")
    purl: str | None = Field(None, description="Package URL for SBOM/SPDX compatibility and deterministic package identity")
    version: str | None = Field(None, description="Semantic version when applicable")
    aliases: list[str] = Field(default_factory=list, description="Alternative names for canonicalization (e.g., ['jwt-simple', 'jose'])")


class Framework(BaseModel):
    """A software framework for application development.

    Entity name should be the framework name.
    Examples: 'React', 'Next.js', 'Rails'
    """
    slug: str = Field(..., description="kebab-case slug, e.g., 'react', 'nextjs', 'rails'")
    version: str | None = Field(None, description="Framework version if applicable")


class ServiceAPI(BaseModel):
    """An external service or API integration (SaaS, third-party).

    Entity name should be the service or API provider name.
    Examples: 'Stripe', 'Auth0', 'Datadog'
    """
    slug: str = Field(..., description="kebab-case slug, e.g., 'stripe', 'auth0', 'datadog'")
    category: str | None = Field(None, description="Service category, e.g., 'payments', 'auth', 'analytics', 'observability'")
    vendor_url: str | None = Field(None, description="Vendor documentation or homepage URL")


class Pattern(BaseModel):
    """A software design pattern or architectural approach.

    Entity name should be the pattern display name.
    Examples: 'Blue-Green Deployment', 'JWT Token Authentication'
    """
    slug: str = Field(..., description="kebab-case slug with domain prefix, e.g., 'deploy__blue_green', 'auth__jwt_tokens'")


class Topic(BaseModel):
    """A high-level development topic or domain area.

    Entity name should be the topic name.
    Examples: 'Performance', 'Security', 'Architecture'
    """
    slug: str = Field(..., description="kebab-case slug, e.g., 'performance', 'security', 'architecture'")


class Decision(BaseModel):
    """An architectural or strategic decision made during development.

    Entity name should be a clear, descriptive decision title.
    Examples: 'Use PostgreSQL for primary database', 'Implement JWT authentication',
              'Deploy with Kubernetes', 'Use ElevenLabs for TTS'

    Decisions have lifecycle: Proposed → Implemented → potentially Superseded.
    When one decision supersedes another, create a SUPERSEDES edge between them.
    """
    decision_id: str = Field(..., description="Canonical decision ID in format d_YYYY_MM_DD_XXXXXXXX (8 hex chars)")
    category: str = Field(..., description="Decision domain: architecture, security, performance, integration, tooling, process")
    status: str = Field(default="Implemented", description="Lifecycle status: Proposed, Implemented, Superseded")
    rationale: str | None = Field(None, description="Brief rationale for the decision (under 500 chars)")
