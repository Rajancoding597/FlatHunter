from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.common.enums import AvailabilityStatus, ContentType, IngestionStatus
from app.ingestion.schemas import CanonicalProperty, FlatHunterExtractionV1
from app.vision.providers import VisionImage


class DraftApprovalError(ValueError):
    """A draft is valid extraction output but is not publishable inventory yet."""

    def __init__(self, missing_fields: list[str]) -> None:
        self.missing_fields = tuple(missing_fields)
        super().__init__(f"Draft needs admin input for: {', '.join(missing_fields)}")


class IngestionService:
    """Persist ingestion provenance while keeping extraction image bytes transient."""

    def __init__(self, db: Optional[Any] = None, vision_provider: Optional[Any] = None, llm: Optional[Any] = None):
        if db is None:
            from app.db.client import get_supabase_client
            db = get_supabase_client()
        if vision_provider is None and llm is None:
            from app.vision.factory import get_vision_provider
            vision_provider = get_vision_provider()
        self.db = db
        # ``llm`` remains a compatibility injection point for older tests/callers.
        self.vision_provider = vision_provider or llm

    def create_session(self, admin_id: UUID, mode: str = "SINGLE") -> UUID:
        result = self.db.table("ingestion_sessions").insert({
            "admin_user_id": str(admin_id), "mode": mode, "status": IngestionStatus.COLLECTING_INFO.value,
        }).execute()
        return result.data[0]["id"]

    def add_text_input(self, session_id: UUID, text: str, group_key: Optional[str] = None) -> str:
        result = self.db.table("ingestion_inputs").insert({
            "ingestion_session_id": str(session_id), "group_key": group_key,
            "input_type": "TEXT", "text_content": text, "is_information_bearing": True,
        }).execute()
        return result.data[0]["id"]

    def add_image_input(
        self,
        session_id: UUID,
        telegram_file_id: str,
        telegram_file_unique_id: Optional[str],
        *,
        is_information_bearing: bool,
        caption: Optional[str] = None,
        group_key: Optional[str] = None,
    ) -> str:
        """Store Telegram references only; image bytes exist only during extraction."""
        result = self.db.table("ingestion_inputs").insert({
            "ingestion_session_id": str(session_id), "group_key": group_key,
            "input_type": "IMAGE", "telegram_file_id": telegram_file_id,
            "telegram_file_unique_id": telegram_file_unique_id, "caption": caption,
            "is_information_bearing": is_information_bearing,
        }).execute()
        return result.data[0]["id"]

    async def complete_session_and_extract(self, session_id: UUID, image_bytes_by_input: Optional[dict[str, bytes]] = None) -> UUID:
        """Extract only information-bearing inputs and transition to the media stage."""
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.EXTRACTING.value}).eq("id", str(session_id)).execute()
        inputs = self.db.table("ingestion_inputs").select("*").eq("ingestion_session_id", str(session_id)).execute()
        text_inputs: list[str] = []
        information_images: list[VisionImage] = []
        image_bytes_by_input = image_bytes_by_input or {}
        for item in inputs.data or []:
            if not item.get("is_information_bearing"):
                continue
            if item.get("input_type") == "TEXT" and item.get("text_content"):
                text_inputs.append(item["text_content"])
            elif item.get("input_type") == "IMAGE":
                image = image_bytes_by_input.get(str(item["id"]))
                if image:
                    information_images.append(VisionImage(data=image, source_id=str(item["id"])))
                if item.get("caption"):
                    text_inputs.append(item["caption"])

        try:
            parsed = await self._extract(images=information_images, text_inputs=text_inputs)
        except Exception as error:
            self.db.table("ingestion_sessions").update({"status": IngestionStatus.FAILED.value}).eq("id", str(session_id)).execute()
            raise ValueError(f"Failed to extract listing: {error}") from error

        draft = self.db.table("listing_drafts").insert(self._draft_payload(parsed, session_id=str(session_id))).execute()
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.COLLECTING_MEDIA.value}).eq("id", str(session_id)).execute()
        return draft.data[0]["id"]

    def complete_media_stage(self, session_id: UUID) -> None:
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.READY_FOR_APPROVAL.value}).eq("id", str(session_id)).execute()

    def approve_draft(self, draft_id: UUID) -> UUID:
        draft_result = self.db.table("listing_drafts").select("*").eq("id", str(draft_id)).execute()
        if not draft_result.data:
            raise ValueError("Listing draft was not found")
        draft = draft_result.data[0]
        if draft.get("content_type") != ContentType.PROPERTY_LISTING.value:
            raise ValueError("Only PROPERTY_LISTING drafts can be approved as inventory")
        if draft.get("extraction_status") == "APPROVED":
            existing = self.db.table("listings").select("id").eq("created_from_draft_id", str(draft_id)).execute()
            if existing.data:
                return existing.data[0]["id"]
        payload = draft["canonical_payload"]
        missing_fields = []
        if payload.get("rent") is None or payload.get("rent", 0) <= 0:
            missing_fields.append("rent")
        if not str(payload.get("locality") or "").strip():
            missing_fields.append("locality")
        if payload.get("listing_type") not in {"ENTIRE_PROPERTY", "PRIVATE_ROOM", "SHARED_ROOM"}:
            missing_fields.append("listing_type")
        if missing_fields:
            raise DraftApprovalError(missing_fields)

        listing_result = self.db.table("listings").insert({
            "listing_type": payload["listing_type"], "city": payload.get("city") or "Hyderabad",
            "locality": payload["locality"], "property_configuration": payload.get("property_configuration"),
            "room_occupancy": payload.get("room_occupancy"), "rent": payload["rent"],
            "maintenance": payload.get("maintenance"), "maintenance_mandatory": payload.get("maintenance_mandatory"),
            "deposit": payload.get("deposit"), "brokerage": payload.get("brokerage"), "available_from": payload.get("available_from"),
            "availability_status": AvailabilityStatus.AVAILABLE.value, "furnishing": payload.get("furnishing"),
            "attached_bathroom": payload.get("attached_bathroom"), "car_parking": payload.get("car_parking"),
            "bike_parking": payload.get("bike_parking"), "extracted_context": draft.get("extracted_context") or {},
            "source_summary": payload.get("source_summary"), "created_from_draft_id": str(draft_id),
        }).execute()
        listing_id = listing_result.data[0]["id"]
        self._persist_sources_and_media(listing_id, draft["ingestion_session_id"])
        self._persist_contacts(listing_id, payload)
        self.db.table("agent_jobs").insert({
            "job_type": "LISTING_CREATED", "idempotency_key": f"LISTING_CREATED:{listing_id}:1", "status": "PENDING",
            "payload": {"listing_id": listing_id, "trigger": "LISTING_CREATED"}, "run_after": "now()",
        }).execute()
        self.db.table("listing_drafts").update({"extraction_status": "APPROVED"}).eq("id", str(draft_id)).execute()
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.APPROVED.value}).eq("id", draft["ingestion_session_id"]).execute()
        return listing_id

    def update_draft_canonical(self, draft_id: UUID, changes: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist explicit admin corrections before approval."""
        draft_result = self.db.table("listing_drafts").select(
            "content_type,canonical_payload,extraction_status"
        ).eq("id", str(draft_id)).execute()
        if not draft_result.data:
            raise ValueError("Listing draft was not found")
        draft = draft_result.data[0]
        if draft.get("content_type") != ContentType.PROPERTY_LISTING.value:
            raise ValueError("Only property listing drafts can be edited")
        if draft.get("extraction_status") != "SUCCESS":
            raise ValueError("Only pending successful drafts can be edited")

        unsupported = sorted(set(changes) - set(CanonicalProperty.model_fields))
        if unsupported:
            raise ValueError(f"Unsupported canonical fields: {', '.join(unsupported)}")
        current_payload = dict(draft.get("canonical_payload") or {})
        canonical_values = {
            field: current_payload.get(field)
            for field in CanonicalProperty.model_fields
        }
        canonical_values.update(changes)
        validated = CanonicalProperty.model_validate(canonical_values).model_dump(mode="json")
        validated["contacts"] = current_payload.get("contacts") or []
        updated = self.db.table("listing_drafts").update({"canonical_payload": validated}).eq(
            "id", str(draft_id)
        ).execute()
        if not updated.data:
            raise RuntimeError("Draft update did not return a saved row")
        return validated

    def reject_draft(self, draft_id: UUID) -> None:
        draft = self.db.table("listing_drafts").select("ingestion_session_id").eq("id", str(draft_id)).execute()
        if not draft.data:
            raise ValueError("Listing draft was not found")
        self.db.table("listing_drafts").update({"extraction_status": "REJECTED"}).eq("id", str(draft_id)).execute()
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.REJECTED.value}).eq("id", draft.data[0]["ingestion_session_id"]).execute()

    def _persist_sources_and_media(self, listing_id: str, session_id: str) -> None:
        inputs = self.db.table("ingestion_inputs").select("*").eq("ingestion_session_id", str(session_id)).order("sort_order").execute()
        for item in inputs.data or []:
            if item.get("input_type") == "TEXT":
                self.db.table("listing_sources").insert({"listing_id": listing_id, "source_type": "ADMIN_TEXT", "raw_text": item.get("text_content")}).execute()
            elif item.get("is_information_bearing"):
                self.db.table("listing_sources").insert({
                    "listing_id": listing_id, "source_type": "ADMIN_SCREENSHOT", "telegram_file_id": item.get("telegram_file_id"),
                    "telegram_file_unique_id": item.get("telegram_file_unique_id"), "raw_text": item.get("caption"),
                }).execute()
            else:
                self.db.table("listing_media").insert({
                    "listing_id": listing_id, "telegram_file_id": item["telegram_file_id"],
                    "telegram_file_unique_id": item.get("telegram_file_unique_id"), "media_type": "PHOTO",
                    "sort_order": item.get("sort_order") or 0, "caption": item.get("caption"),
                }).execute()

    def _persist_contacts(self, listing_id: str, payload: dict) -> None:
        for contact in payload.get("contacts") or []:
            contact_result = self.db.table("contacts").insert({"listing_id": listing_id, "name": contact.get("name"), "role": contact.get("role", "UNKNOWN")}).execute()
            channels = contact.get("channels") or [
                {"type": "PHONE", "value": phone} for phone in contact.get("phones") or []
            ]
            for channel in channels:
                self.db.table("contact_channels").insert({
                    "contact_id": contact_result.data[0]["id"], "type": channel["type"], "value": channel["value"],
                    "explicit": True, "is_usable": True,
                }).execute()

    async def complete_bulk_session_and_extract(self, session_id: UUID, image_bytes_by_input: Optional[dict[str, bytes]] = None) -> list[UUID]:
        """Create one independently reviewable draft for every text group or screenshot."""
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.EXTRACTING.value}).eq("id", str(session_id)).execute()
        inputs = self.db.table("ingestion_inputs").select("*").eq("ingestion_session_id", str(session_id)).order("sort_order").execute()
        images = image_bytes_by_input or {}
        drafts: list[UUID] = []
        for item in inputs.data or []:
            if item.get("input_type") == "TEXT" and item.get("text_content"):
                units = [(f"text-{index}", [], [text]) for index, text in enumerate(
                    (part.strip() for part in item["text_content"].split("---") if part.strip()), start=1
                )]
            elif item.get("input_type") == "IMAGE" and images.get(str(item["id"])):
                units = [(str(item["id"]), [VisionImage(data=images[str(item["id"])], source_id=str(item["id"]))], [item["caption"]] if item.get("caption") else [])]
            else:
                units = []
            for group_key, unit_images, unit_text in units:
                parsed = await self._extract(images=unit_images, text_inputs=unit_text)
                payload = self._draft_payload(parsed, session_id=str(session_id), group_key=group_key)
                result = self.db.table("listing_drafts").insert(payload).execute()
                drafts.append(result.data[0]["id"])
        self.db.table("ingestion_sessions").update({"status": IngestionStatus.READY_FOR_APPROVAL.value}).eq("id", str(session_id)).execute()
        return drafts

    async def _extract(self, *, images: list[VisionImage], text_inputs: list[str]) -> FlatHunterExtractionV1:
        if hasattr(self.vision_provider, "extract_listing"):
            # Text entered during the admin ingestion session is authoritative only
            # when it is clearly phrased as a correction; the shared prompt preserves
            # ordinary copied listing text as source information.
            return await self.vision_provider.extract_listing(images=images, text_inputs=[], admin_notes=text_inputs)
        legacy_prompt: list[Any] = list(text_inputs)
        legacy_prompt.extend({"mime_type": image.mime_type, "data": image.data} for image in images)
        return await self.vision_provider.generate_structured(legacy_prompt, FlatHunterExtractionV1)

    def _draft_payload(self, parsed: FlatHunterExtractionV1, *, session_id: str, group_key: Optional[str] = None) -> dict[str, Any]:
        canonical = parsed.canonical.model_dump(mode="json")
        canonical["contacts"] = [contact.model_dump(mode="json") for contact in parsed.contacts]
        context = dict(parsed.additional_attributes)
        context["uncertain_fields"] = parsed.uncertain_fields
        context["extraction_notes"] = parsed.extraction_notes
        metadata = dict(getattr(self.vision_provider, "last_metadata", {}) or {})
        metadata.update({
            "provider": getattr(self.vision_provider, "provider_name", "legacy"),
            "model": getattr(self.vision_provider, "model_name", None),
            "schema": "FlatHunterExtractionV1",
        })
        return {
            "ingestion_session_id": session_id,
            "group_key": group_key,
            "content_type": parsed.content_type.value,
            "canonical_payload": canonical,
            "extracted_context": context,
            "conflicts": [conflict.model_dump(mode="json") for conflict in parsed.conflicts],
            "model_metadata": metadata,
            "extraction_status": "SUCCESS",
        }
