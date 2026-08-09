"""Validated, provider-independent extraction contract for listing ingestion."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExtractionContentType(StrEnum):
    PROPERTY_LISTING = "PROPERTY_LISTING"
    RENTER_REQUIREMENT = "RENTER_REQUIREMENT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ExtractedListingType(StrEnum):
    ENTIRE_PROPERTY = "ENTIRE_PROPERTY"
    PRIVATE_ROOM = "PRIVATE_ROOM"
    SHARED_ROOM = "SHARED_ROOM"
    UNKNOWN = "UNKNOWN"


class ExtractedFurnishing(StrEnum):
    FURNISHED = "FURNISHED"
    SEMI_FURNISHED = "SEMI_FURNISHED"
    UNFURNISHED = "UNFURNISHED"
    UNKNOWN = "UNKNOWN"


class ContactRole(StrEnum):
    OWNER = "OWNER"
    BROKER = "BROKER"
    CURRENT_TENANT = "CURRENT_TENANT"
    UNKNOWN = "UNKNOWN"


class ContactChannelType(StrEnum):
    PHONE = "PHONE"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"
    TELEGRAM = "TELEGRAM"


class StrictExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactChannel(StrictExtractionModel):
    type: ContactChannelType
    value: str = Field(min_length=1)


class PropertyContact(StrictExtractionModel):
    name: str | None = None
    role: ContactRole = ContactRole.UNKNOWN
    channels: list[ContactChannel] = Field(min_length=1)


class CanonicalProperty(StrictExtractionModel):
    listing_type: ExtractedListingType | None = None
    property_configuration: str | None = None
    city: str = "Hyderabad"
    locality: str | None = None
    location_text: str | None = None
    landmark: str | None = None
    rent: int | None = Field(default=None, ge=0)
    maintenance: int | None = Field(default=None, ge=0)
    deposit: int | None = Field(default=None, ge=0)
    brokerage: int | None = Field(default=None, ge=0)
    available_from: date | None = None
    furnishing: ExtractedFurnishing | None = None
    attached_bathroom: bool | None = None
    car_parking: bool | None = None
    bike_parking: bool | None = None


class ExtractionConflict(StrictExtractionModel):
    field: str
    values: list[Any] = Field(default_factory=list)
    explanation: str


class FlatHunterExtractionV1(StrictExtractionModel):
    """Moderate canonical contract plus lossless flexible rental context."""

    content_type: ExtractionContentType = ExtractionContentType.UNKNOWN
    canonical: CanonicalProperty = Field(default_factory=CanonicalProperty)
    contacts: list[PropertyContact] = Field(default_factory=list)
    additional_attributes: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[ExtractionConflict] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    extraction_notes: list[str] = Field(default_factory=list)


# Backward-compatible import for callers not yet moved to the dedicated vision boundary.
ListingExtractionResponse = FlatHunterExtractionV1
