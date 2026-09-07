"""
Pydantic models for API request/response schemas.
"""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    whatsapp_connected: bool = False
    version: str = "0.1.0"


class WebhookPayload(BaseModel):
    """Incoming Evolution API webhook payload (flexible structure)."""
    event: str = ""
    instance: str = ""
    data: dict = Field(default_factory=dict)


class QueryRequest(BaseModel):
    """Direct query request (for testing without WhatsApp)."""
    question: str
    course: str | None = None
    include_sources: bool = True


class QueryResponse(BaseModel):
    """Response from the AI agent."""
    answer: str
    confidence: float = 0.0
    sources: list[dict] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)


class ScheduleResponse(BaseModel):
    """Upcoming schedule response."""
    events: list[dict] = Field(default_factory=list)
    formatted: str = ""


class DeleteEventRequest(BaseModel):
    """Request to delete calendar events."""
    query: str
    date: str | None = None


class DeleteEventResponse(BaseModel):
    """Response after deleting calendar events."""
    deleted_events: list[str] = Field(default_factory=list)
    message: str = ""


class IngestRequest(BaseModel):
    """File ingestion request."""
    file_path: str
    content_type: str = "lecture"  # lecture, exam, section, handwritten
    course: str | None = None
