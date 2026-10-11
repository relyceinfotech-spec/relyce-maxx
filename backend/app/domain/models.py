from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

Mode = Literal["auto", "normal", "knowledge", "web", "deep"]


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    conversation_id: UUID | None = None
    post_id: UUID | None = None
    mode: Mode = "auto"
    idempotency_key: UUID

    @field_validator("prompt")
    @classmethod
    def meaningful_prompt(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Prompt cannot be blank")
        return value


class Route(BaseModel):
    mode: Literal["normal", "knowledge", "web", "deep"]
    retrieve: bool = False
    reason: str = Field(max_length=200)


class Plan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)


class Source(BaseModel):
    id: str
    url: str
    title: str
    text: str
    published_at: str | None = None
    canonical_url: str | None = None


class Passage(BaseModel):
    source_id: str = Field(pattern=r"^(S|K|P)\d+$")
    passage_id: str = Field(pattern=r"^(S|K|P)\d+-P\d+$")
    text: str = Field(min_length=20, max_length=700)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


class Page(BaseModel):
    source_id: str = Field(pattern=r"^S\d+$")
    url: str
    canonical_url: str
    title: str
    text: str = Field(max_length=6000)
    published_at: str | None = None
    date_confidence: Literal["document", "unknown"] = "unknown"
    search_date: str | None = None
    content_type: str
    status: int
    truncated: bool = False
    passages: list[Passage]

    @model_validator(mode="after")
    def exact_passages(self):
        ids = set()
        for passage in self.passages:
            if (
                passage.source_id != self.source_id
                or not passage.passage_id.startswith(self.source_id + "-P")
                or passage.passage_id in ids
                or passage.end_offset > len(self.text)
                or passage.start_offset >= passage.end_offset
                or self.text[passage.start_offset : passage.end_offset] != passage.text
            ):
                raise ValueError("Passage must address exact captured source text")
            ids.add(passage.passage_id)
        return self


class Claim(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    source_id: str
    quote: str = Field(min_length=20, max_length=1200)


class Report(BaseModel):
    answer: str = Field(min_length=1, max_length=20000)
    claims: list[Claim] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    sources: list[Source] = Field(default_factory=list)
    verified: bool = False


class PassageClaim(Claim):
    passage_id: str = Field(pattern=r"^(S|K|P)\d+-P\d+$")


class ChatReport(Report):
    claims: list[PassageClaim] = Field(default_factory=list, max_length=30)
    citations: list["Citation"] = Field(default_factory=list, max_length=30)
    sentence_claims: list["SentenceClaims"] = Field(default_factory=list, max_length=80)


class SentenceClaims(BaseModel):
    sentence_index: int = Field(ge=0, le=79)
    claim_indices: list[int] = Field(min_length=1, max_length=30)


class VerificationIssue(BaseModel):
    category: Literal[
        "contradiction",
        "qualification",
        "missing_claim",
        "missing_citation",
        "temporal_mismatch",
        "incomplete_coverage",
        "unsupported_scope",
    ]
    sentence_indices: list[int] = Field(default_factory=list, max_length=80)
    claim_indices: list[int] = Field(default_factory=list, max_length=30)
    passage_ids: list[str] = Field(default_factory=list, max_length=30)


class Citation(BaseModel):
    source_id: str
    passage_id: str
    url: str
    title: str
    claim_indices: list[int]


class Verification(BaseModel):
    supported: list[bool]
    contradictions: list[str]


class ChatVerification(Verification):
    # Required independent coverage judgment, separate from the submitted claim list.
    supported: list[bool] = Field(max_length=30)
    contradictions: list[str] = Field(max_length=10)
    answer_coverage_supported: bool
    coverage_failures: list[str] = Field(max_length=10)
    issues: list[VerificationIssue] = Field(default_factory=list, max_length=10)


class UpdateDecision(BaseModel):
    changed: bool
    note: str = Field(min_length=1, max_length=500)


class Draft(BaseModel):
    title: str = Field(min_length=5, max_length=180)
    summary: str = Field(min_length=20, max_length=500)
    body: str = Field(min_length=50, max_length=20000)
    topic_slugs: list[str] = Field(min_length=1, max_length=5)
    claims: list[Claim] = Field(min_length=1, max_length=20)


class PostFromResearch(BaseModel):
    research_job_id: UUID
    post_id: UUID | None = None
    correction_note: str | None = Field(None, max_length=1000)


class Review(BaseModel):
    version_id: UUID
    decision: Literal["approve", "reject"]
    note: str = Field("", max_length=2000)


class Interest(BaseModel):
    topic_id: UUID
    weight: float = Field(1, ge=0, le=1)
    blocked: bool = False


class Interaction(BaseModel):
    post_id: UUID
    kind: Literal["save", "like", "hide", "not_interested", "read"]


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    topic_ids: list[UUID] = Field(min_length=1, max_length=5)
    poll_minutes: int = Field(60, ge=30, le=10080)
    enabled: bool = False
    trust_tier: Literal["primary", "secondary"] = "secondary"


class QueryInput(BaseModel):
    topic_id: UUID
    query: str = Field(min_length=3, max_length=300)
    frequency_minutes: int = Field(120, ge=60, le=10080)
    enabled: bool = False
