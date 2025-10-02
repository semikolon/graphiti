from pydantic import BaseModel, Field


class ProgrammingLanguage(BaseModel):
    """A programming language, such as Python, Go, or Rust."""
    pass


class Project(BaseModel):
    """A software project, codebase, or development effort."""
    name: str = Field(..., description="Project name for display")
    slug: str = Field(..., description="Unique project identifier for canonical identity")
    description: str = Field(..., description="A brief description of what the project does or its purpose.")


class Library(BaseModel):
    """A software library or package dependency."""
    name: str = Field(..., description="Canonical library name, e.g., 'jsonwebtoken', 'ioredis'")
    slug: str = Field(..., description="kebab-case stable slug for unique identification, e.g., 'jsonwebtoken'")
    language: str | None = Field(None, description="Primary programming language/ecosystem, e.g., 'TypeScript', 'Python', 'Go'")
    purl: str | None = Field(None, description="Package URL for SBOM/SPDX compatibility and deterministic package identity")
    version: str | None = Field(None, description="Semantic version when applicable")
    aliases: list[str] = Field(default_factory=list, description="Alternative names for canonicalization (e.g., ['jwt-simple', 'jose'])")


class Framework(BaseModel):
    """A software framework for application development."""
    name: str = Field(..., description="Framework name, e.g., 'React', 'Next.js', 'Rails'")
    slug: str = Field(..., description="kebab-case slug, e.g., 'react', 'nextjs', 'rails'")
    version: str | None = Field(None, description="Framework version if applicable")


class ServiceAPI(BaseModel):
    """An external service or API integration (SaaS, third-party)."""
    name: str = Field(..., description="External service or API provider, e.g., 'Stripe', 'Auth0', 'Datadog'")
    slug: str = Field(..., description="kebab-case slug, e.g., 'stripe', 'auth0', 'datadog'")
    category: str | None = Field(None, description="Service category, e.g., 'payments', 'auth', 'analytics', 'observability'")
    vendor_url: str | None = Field(None, description="Vendor documentation or homepage URL")


class Pattern(BaseModel):
    """A software design pattern or architectural approach."""
    name: str = Field(..., description="Pattern display name, e.g., 'Blue-Green Deployment', 'JWT Token Authentication'")
    slug: str = Field(..., description="kebab-case slug with domain prefix, e.g., 'deploy__blue_green', 'auth__jwt_tokens'")


class Topic(BaseModel):
    """A high-level development topic or domain area."""
    name: str = Field(..., description="High-level topic, e.g., 'Performance', 'Security', 'Architecture'")
    slug: str = Field(..., description="kebab-case slug, e.g., 'performance', 'security', 'architecture'")
