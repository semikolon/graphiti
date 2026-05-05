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
    """An architectural or technical decision made during development.

    Entity name should be a clear, descriptive decision title.
    Examples: 'Use PostgreSQL for primary database', 'Implement JWT authentication',
              'Deploy with Kubernetes', 'Use ElevenLabs for TTS'

    Decisions have lifecycle: Proposed → Implemented → potentially Superseded.
    When one decision supersedes another, create a SUPERSEDES edge between them.

    For non-technical decisions, use BusinessRule, PolicyDecision, WorkflowChoice, or ExternalConstraint.
    """
    decision_id: str = Field(..., description="Canonical decision ID in format d_YYYY_MM_DD_XXXXXXXX (8 hex chars)")
    category: str = Field(..., description="Decision domain: architecture, security, performance, integration, tooling, process")
    status: str = Field(default="Implemented", description="Lifecycle status: Proposed, Implemented, Superseded")
    rationale: str | None = Field(None, description="Brief rationale for the decision (under 500 chars)")


class BusinessRule(BaseModel):
    """A domain-specific business rule, formula, or threshold we chose to implement.

    Entity name should describe the rule clearly.
    Examples: 'Rent calculated by days stayed', 'Confidence weight 95/90/70 hierarchy',
              'Remainder distributed to largest fractional parts', 'Critical severity triggers Phase 3'

    BusinessRules are internal choices with rationale - we decided this formula/threshold.
    They may be superseded when business logic evolves.
    """
    decision_id: str = Field(..., description="Canonical decision ID in format d_YYYY_MM_DD_XXXXXXXX (8 hex chars)")
    category: str = Field(..., description="Rule domain: pricing, calculation, validation, threshold, workflow-logic")
    status: str = Field(default="Implemented", description="Lifecycle status: Proposed, Implemented, Superseded")
    conditions: str | None = Field(None, description="When this rule applies (under 300 chars)")
    rationale: str | None = Field(None, description="Why this formula/threshold/logic (under 500 chars)")
    exceptions: str | None = Field(None, description="When the rule doesn't apply (under 300 chars)")


class PolicyDecision(BaseModel):
    """An organizational or strategic decision affecting how we operate.

    Entity name should describe the policy clearly.
    Examples: 'Prefer long-term tenants', 'Cost optimization threshold $1000/year',
              'Cyan-only success states', 'Handbook public deployment deferred'

    PolicyDecisions include brand decisions, strategic choices, and governance rules.
    They may have review dates and can be superseded.
    """
    decision_id: str = Field(..., description="Canonical decision ID in format d_YYYY_MM_DD_XXXXXXXX (8 hex chars)")
    category: str = Field(..., description="Policy domain: governance, brand, strategy, recruitment, cost, feature-roadmap")
    status: str = Field(default="Implemented", description="Lifecycle status: Proposed, Implemented, Superseded")
    deciders: str | None = Field(None, description="Who made this decision (role/person/team)")
    rationale: str | None = Field(None, description="Why we chose this (under 500 chars)")
    review_date: str | None = Field(None, description="When to revisit this policy (ISO date or 'quarterly'/'annually')")


class WorkflowChoice(BaseModel):
    """A process architecture or workflow design decision.

    Entity name should describe the workflow/process.
    Examples: 'Two-phase QC gate', 'Parallel Phase 1 and 2 execution',
              'Single active task model', 'Git-backed handbook proposals'

    WorkflowChoices define how we do things - ceremonies, processes, procedures.
    They may be superseded as processes evolve.
    """
    decision_id: str = Field(..., description="Canonical decision ID in format d_YYYY_MM_DD_XXXXXXXX (8 hex chars)")
    category: str = Field(..., description="Workflow domain: development, review, deployment, testing, ceremony, operations")
    status: str = Field(default="Implemented", description="Lifecycle status: Proposed, Implemented, Superseded")
    rationale: str | None = Field(None, description="Why this workflow/process (under 500 chars)")
    procedure: str | None = Field(None, description="Brief description of how it works (under 500 chars)")


class ExternalConstraint(BaseModel):
    """An external regulation, legal requirement, or domain fact we must comply with.

    Entity name should describe the constraint clearly.
    Examples: 'Bolagsverket January 2025 mandate', 'BRF org number 70-79 prefix',
              'GDPR data retention limits', 'Swedish accounting depreciation rules'

    ExternalConstraints are NOT our decisions - they're imposed by external authorities.
    No decision_id (we didn't decide this). No rationale (it's mandated).
    Track source, compliance status, and review dates.
    """
    source: str = Field(..., description="Authoritative source: law name, regulatory body, professional standard")
    constraint_type: str = Field(..., description="Type: regulation, legal, compliance, domain-fact, professional-standard")
    scope: str | None = Field(None, description="What this affects in our system (under 300 chars)")
    compliance_status: str = Field(default="Compliant", description="Status: Compliant, In Progress, Non-Compliant, N/A")
    effective_date: str | None = Field(None, description="When this took/takes effect (ISO date)")
    review_date: str | None = Field(None, description="When to check for updates (ISO date or 'annually')")


# --- Task management entities (Fyr personal assistant) ---


class Task(BaseModel):
    """An actionable item with status lifecycle and dependency tracking.

    Tasks are the atomic unit of work. They can represent anything from
    'pay the electricity bill' to 'plan next week's meals' to 'set up
    the new router'. Each task has a status, optional priority, and
    optional energy/time estimates.

    Entity name should be a clear, actionable task title.
    Examples: 'Pay rent March 2026', 'Plan weekly meals', 'Set up new router'

    Dependencies use DEPENDS_ON edges (one canonical direction only):
    "Deploy food system" DEPENDS_ON "Set up router" means router must finish first.
    """
    status: str = Field(
        default="open",
        description="Lifecycle status: open, in_progress, blocked, done, deferred"
    )
    priority: int | None = Field(
        None,
        description="1 (highest) to 5 (lowest), or null (unset — heartbeat derives from deps/deadlines)"
    )
    energy_level: str | None = Field(
        None,
        description="Required energy: low, medium, high — matches user's current energy for 'what can I do now?' queries"
    )
    estimated_minutes: int | None = Field(
        None,
        description="Rough time estimate in minutes — enables 'I have 15 minutes, what fits?' queries"
    )
    due_date: str | None = Field(
        None,
        description="ISO 8601 date (e.g. '2026-03-25'). For shared deadlines, use a Deadline entity + DUE_BY edge instead"
    )
    recurrence: str | None = Field(
        None,
        description="Recurrence pattern: none, daily, weekly, monthly, or cron expression"
    )
    domain: str | None = Field(
        None,
        description="Life domain: food, finance, household, health, work, personal"
    )
    context: str | None = Field(
        None,
        description="GTD-style context: at_computer, errands, phone_call, or free-form"
    )
    external_source: str | None = Field(
        None,
        description="Connector name if scanned from an external system: 'ticktick', 'workflowy', 'keep', 'todomd'. None for Fyr-native tasks. Added 2026-05-05 per master-todo-system v1 spec (~/Projects/fyr/.claude/specs/master-todo-system/)."
    )
    external_id: str | None = Field(
        None,
        description="Stable identifier from the external source for re-finding the same task across scans. Examples: TickTick task UUID; Workflowy node ID; Keep '<note_id>:<item_id>'; TODO.md '<filepath>:<linenum>'. Added 2026-05-05."
    )


class Deadline(BaseModel):
    """A temporal constraint that may apply to one or more tasks.

    Use for shared deadlines that affect multiple tasks (e.g., 'REKO order
    deadline' applies to 'decide meals' + 'check inventory' + 'place order').
    For simple single-task due dates, use Task.due_date instead.

    Entity name should describe the deadline clearly.
    Examples: 'REKO order deadline March 15', 'Electricity bill due date', 'Tax filing deadline'
    """
    date: str = Field(..., description="ISO 8601 date (e.g. '2026-03-25')")
    hard: bool = Field(
        default=True,
        description="True = immovable (bill due date, legal deadline). False = soft/aspirational"
    )
    consequence: str | None = Field(
        None,
        description="What happens if missed? e.g. 'late fee 50kr', 'inkasso risk', 'miss REKO pickup'"
    )


class Routine(BaseModel):
    """A recurring pattern of activity — habits, rituals, periodic processes.

    Different from a recurring Task: a Routine is the PATTERN, while Tasks
    are individual INSTANCES. 'Weekly meal planning' is a Routine that
    generates Tasks each week.

    Entity name should describe the routine clearly.
    Examples: 'Weekly meal planning', 'Monthly rent payment', 'Daily exercise'
    """
    frequency: str = Field(
        ...,
        description="Recurrence frequency: daily, weekly, monthly, or cron expression"
    )
    time_of_day: str | None = Field(
        None,
        description="Preferred time: morning, afternoon, evening, anytime"
    )
    domain: str | None = Field(
        None,
        description="Life domain: food, finance, household, health, work, personal"
    )
    active: bool = Field(
        default=True,
        description="Can be paused without deletion"
    )


# --- Household / interpersonal entities (Ruby household context) ---


class Person(BaseModel):
    """A human known to the household — family, housemates, friends, neighbors,
    landlords, contractors, professionals referenced in conversations.

    Entity name should be the person's primary first name (or nickname) for
    display, e.g., 'Rasmus', 'Lisa', 'Fredrik', 'Ingrid'. Use 'full_name' to
    disambiguate when multiple people share a first name.

    Relationships are represented via edges (PROMISED_BY / PROMISED_TO / ATTENDED /
    LIVES_WITH / WORKS_FOR / etc.) that Graphiti extracts from context. Keep the
    Person node itself stable; let the graph capture the dynamics.
    """
    full_name: str | None = Field(
        None,
        description="Full name (first + last) when disambiguation is needed"
    )
    role: str | None = Field(
        None,
        description="Relationship type: housemate, family, partner, friend, landlord, contractor, professional, neighbor, colleague"
    )
    household: bool = Field(
        default=False,
        description="True if this person lives in the Sarpetorp household"
    )
    notes: str | None = Field(
        None,
        description="Stable context: occupation, preferences, shared history (under 300 chars). Avoid ephemeral state."
    )


class Commitment(BaseModel):
    """An interpersonal promise — something one person said they would do,
    usually on behalf of or to another person.

    Distinct from Task (personal backlog): a Commitment captures PROVENANCE
    (who promised whom, when stated, in what channel). 'Fredrik promised to
    pay Rasmus back by Friday' is a Commitment; 'Pay Rasmus back 500kr' on
    Fredrik's own list would be a Task fulfilled by that Commitment. They
    may co-exist — use a FULFILLS edge (Task)-[:FULFILLS]->(Commitment).

    Link Commitments to People via PROMISED_BY and PROMISED_TO edges. Link to
    the originating Meeting, Email, or Message episode via a MADE_IN edge so
    the raw context is always one hop away.

    Entity name should describe the commitment concisely.
    Examples: 'Pay Rasmus 500kr for groceries', 'Fix kitchen tap by weekend',
              'Send landlord quarterly report', 'Bring hammer Sunday'
    """
    status: str = Field(
        default="open",
        description="Lifecycle: open, fulfilled, broken, revoked, superseded"
    )
    due_at: str | None = Field(
        None,
        description="ISO 8601 date or datetime when the commitment is due (e.g. '2026-04-25' or '2026-04-25T17:00:00')"
    )
    promised_at: str | None = Field(
        None,
        description="ISO 8601 when the commitment was made — the moment of speaking/writing it"
    )
    channel: str | None = Field(
        None,
        description="How it was communicated: voice_conversation, email, beeper, sms, meeting, written_agreement"
    )
    context: str | None = Field(
        None,
        description="Stated conditions or caveats that qualify the commitment (under 300 chars)"
    )


class Meeting(BaseModel):
    """A bounded conversation episode — a house discussion, phone/video call,
    or in-person meeting. Serves as an anchor for who said what, and when.

    Distinct from Routine (the PATTERN, e.g. 'weekly house sync') — a Meeting
    is the INSTANCE on a specific date. Distinct from Task — a Meeting is an
    event that occurred, not work to do. Distinct from a voice conversation
    with Ruby — those are daemon_conversation Episodes, not Meetings unless
    other Persons participated.

    Link to Persons via ATTENDED edges. Link to Commitments made during the
    meeting via MADE_IN edges. The raw transcript typically lives outside the
    graph (file, audio, email thread); use transcript_ref to point to it and
    keep the graph lean.

    Entity name should describe the meeting concisely.
    Examples: 'House sync 2026-04-15', 'Call with landlord re: heating 2026-03-22',
              'REKO planning dinner 2026-04-10'
    """
    date: str = Field(
        ...,
        description="ISO 8601 date or datetime when the meeting took place"
    )
    participants: list[str] = Field(
        default_factory=list,
        description="Names of Persons present (use Person entity names for linkability)"
    )
    location: str | None = Field(
        None,
        description="Where: living_room, kitchen, atelier, phone_call, video_call, email_thread, chat"
    )
    topic: str | None = Field(
        None,
        description="Primary topic or purpose (under 200 chars)"
    )
    transcript_ref: str | None = Field(
        None,
        description="Pointer to the raw source: file path, episode_id, message-thread ID, or email-thread ID. Raw text stays out of the graph."
    )
