from typing import List, Optional, Any, Dict
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict
from uuid import UUID
from app.common.enums import (
    UserRole, SearchStatus, ListingType, ContentType,
    AvailabilityStatus, MatchStatus, ConversationStatus,
    VisitStatus, ContactChannelType, IngestionStatus
)

class DBModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class User(DBModel):
    id: UUID
    telegram_user_id: int
    telegram_username: Optional[str] = None
    display_name: Optional[str] = None
    role: UserRole
    created_at: datetime
    updated_at: datetime

class SearchSession(DBModel):
    id: UUID
    user_id: UUID
    status: SearchStatus
    version: int = 1
    city: str
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    last_activated_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

class SearchRequirement(DBModel):
    id: UUID
    search_id: UUID
    listing_types: List[ListingType]
    preferred_locations: List[str]
    acceptable_locations: List[str] = []
    excluded_locations: List[str] = []
    work_location: Optional[str] = None
    target_rent: int
    max_rent: int
    preferred_move_in_date: Optional[date] = None
    latest_move_in_date: Optional[date] = None
    preferred_property_configurations: Optional[List[str]] = None
    core_preferences: Dict[str, Any] = {}
    additional_preferences: Dict[str, Any] = {}
    scoring_weights: Dict[str, Any] = {}
    raw_requirement_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class IngestionSession(DBModel):
    id: UUID
    admin_user_id: UUID
    mode: str
    status: IngestionStatus
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

class ListingDraft(DBModel):
    id: UUID
    ingestion_session_id: UUID
    group_key: Optional[str] = None
    content_type: ContentType
    canonical_payload: Dict[str, Any]
    extracted_context: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []
    extraction_status: str
    model_metadata: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

class Listing(DBModel):
    id: UUID
    listing_type: ListingType
    city: str
    locality: str
    location_text: Optional[str] = None
    landmark: Optional[str] = None
    property_configuration: Optional[str] = None
    room_occupancy: Optional[str] = None
    rent: int
    maintenance: Optional[int] = None
    maintenance_mandatory: Optional[bool] = None
    deposit: Optional[int] = None
    brokerage: Optional[int] = None
    currency: str = "INR"
    available_from: Optional[date] = None
    availability_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    last_verified_at: Optional[datetime] = None
    furnishing: Optional[str] = None
    attached_bathroom: Optional[bool] = None
    car_parking: Optional[bool] = None
    bike_parking: Optional[bool] = None
    balcony: Optional[bool] = None
    pets_allowed: Optional[bool] = None
    power_backup: Optional[bool] = None
    gated_community: Optional[bool] = None
    extracted_context: Dict[str, Any] = {}
    source_summary: Optional[str] = None
    created_from_draft_id: Optional[UUID] = None
    version: int = 1
    created_at: datetime
    updated_at: datetime

class Match(DBModel):
    id: UUID
    search_id: UUID
    listing_id: UUID
    status: MatchStatus
    fit_score: Optional[float] = None
    information_completeness: Optional[float] = None
    hard_rejection_reasons: List[Any] = []
    positive_reasons: List[Any] = []
    missing_information: List[Any] = []
    soft_context_evaluation: Dict[str, Any] = {}
    score_breakdown: Dict[str, Any] = {}
    search_version: int = 1
    listing_version: int = 1
    created_at: datetime
    updated_at: datetime

class Conversation(DBModel):
    id: UUID
    search_id: UUID
    listing_id: UUID
    contact_id: UUID
    status: ConversationStatus
    active_channel_id: Optional[UUID] = None
    outreach_approved_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    follow_up_count: int = 0
    created_at: datetime
    updated_at: datetime

class AgentJob(DBModel):
    id: UUID
    job_type: str
    idempotency_key: Optional[str] = None
    status: str
    payload: Dict[str, Any]
    run_after: datetime
    attempts: int = 0
    last_error: Optional[str] = None
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
