from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.common.enums import SearchStatus
from app.db.models import SearchSession
from app.requirements.schemas import RequirementEditResponse, RequirementExtractionResponse


class RequirementService:
    """Owns deterministic search-profile validation and lifecycle writes."""

    def __init__(self, db: Optional[Any] = None, llm: Optional[Any] = None):
        if db is None:
            from app.db.client import get_supabase_client
            db = get_supabase_client()
        if llm is None:
            from app.llm.gemini import get_llm_provider
            llm = get_llm_provider()
        self.db = db
        self.llm = llm

    async def parse_requirements(self, text: str) -> RequirementExtractionResponse:
        prompt = f'''
        You are a friendly rental search assistant having a conversation with a renter.
        Analyze the conversation below and extract rental requirements.

        Conversation:
        "{text}"

        RULES:
        - Listing type, location, maximum budget, and move-in timing are mandatory.
        - Missing mandatory information means is_complete must be false.
        - Ask only the single most important missing item in a friendly 1-2 sentence follow-up.
        - Never invent values. Keep unknown facts null.
        - Return JSON matching RequirementExtractionResponse.
        '''
        try:
            return await self.llm.generate_structured(prompt, RequirementExtractionResponse)
        except Exception as error:
            raise ValueError(f"Failed to parse requirements: {error}") from error

    async def parse_search_edit(self, text: str, current_requirements: dict) -> RequirementEditResponse:
        """Extract only changes; omitted fields intentionally retain stored values."""
        prompt = f'''
        You update an existing Hyderabad rental search. Extract ONLY values the renter
        explicitly changes in their latest message. Do not infer or repeat unchanged fields.

        Current saved requirements: {current_requirements}
        Renter update: "{text}"

        Rules:
        - Return JSON matching RequirementEditResponse.
        - Fields not changed must be null (not empty lists or invented values).
        - For location additions/removals, return the complete resulting list using the
          current values and the renter's explicit change.
        - Do not clear a field unless the renter explicitly asks to remove or clear it.
        '''
        try:
            return await self.llm.generate_structured(prompt, RequirementEditResponse)
        except Exception as error:
            raise ValueError(f"Failed to parse search update: {error}") from error

    @staticmethod
    def missing_core_requirements(requirements: RequirementExtractionResponse) -> list[str]:
        missing = []
        if not requirements.listing_types:
            missing.append("listing type")
        if not (requirements.preferred_locations or requirements.acceptable_locations):
            missing.append("location")
        if requirements.max_rent is None or requirements.max_rent <= 0:
            missing.append("maximum budget")
        if not (requirements.preferred_move_in_date or requirements.latest_move_in_date):
            missing.append("move-in timing")
        return missing

    def create_draft_search(self, user_id: UUID, requirements: RequirementExtractionResponse, raw_text: str) -> SearchSession:
        missing = self.missing_core_requirements(requirements)
        if missing:
            raise ValueError(f"Cannot create a search without: {', '.join(missing)}")
        session_result = self.db.table("search_sessions").insert({
            "user_id": str(user_id), "status": SearchStatus.DRAFT.value,
            "city": "Hyderabad", "version": 1,
        }).execute()
        if not session_result.data:
            raise RuntimeError("Failed to create search session")
        session = SearchSession(**session_result.data[0])
        self._insert_requirements(session.id, requirements, raw_text)
        return session

    def activate_search(self, user_id: UUID, search_id: UUID) -> SearchSession:
        session_result = self.db.table("search_sessions").select("*").eq("id", str(search_id)).eq("user_id", str(user_id)).execute()
        if not session_result.data:
            raise ValueError("Search was not found for this renter")
        session_data = session_result.data[0]
        if session_data["status"] == SearchStatus.CLOSED.value:
            raise ValueError("Closed searches cannot be activated")
        requirements_result = self.db.table("search_requirements").select("*").eq("search_id", str(search_id)).execute()
        if not requirements_result.data:
            raise ValueError("Search requirements are missing")
        missing = self._missing_persisted_core_requirements(requirements_result.data[0])
        if missing:
            raise ValueError(f"Cannot activate a search without: {', '.join(missing)}")

        if session_data["status"] != SearchStatus.ACTIVE.value:
            now = self._now()
            updated = self.db.table("search_sessions").update({
                "status": SearchStatus.ACTIVE.value,
                "started_at": session_data.get("started_at") or now,
                "last_activated_at": now,
            }).eq("id", str(search_id)).eq("user_id", str(user_id)).execute()
            if not updated.data:
                raise RuntimeError("Failed to activate search")
            session_data = updated.data[0]

        version = session_data.get("version", 1)
        self._queue_match_job("MATCH_ACTIVE_SEARCH", search_id, version, "SEARCH_STARTED")
        return SearchSession(**session_data)

    def create_search(self, user_id: UUID, requirements: RequirementExtractionResponse, raw_text: str) -> SearchSession:
        """Compatibility helper for existing callers during the handler migration."""
        draft = self.create_draft_search(user_id, requirements, raw_text)
        return self.activate_search(user_id, draft.id)

    def update_draft_search(self, user_id: UUID, search_id: UUID, requirements: RequirementExtractionResponse, raw_text: str) -> None:
        """Replace a renter-owned draft profile before it is activated."""
        missing = self.missing_core_requirements(requirements)
        if missing:
            raise ValueError(f"Cannot save a search without: {', '.join(missing)}")
        session_result = self.db.table("search_sessions").select("id,status").eq("id", str(search_id)).eq("user_id", str(user_id)).execute()
        if not session_result.data or session_result.data[0]["status"] != SearchStatus.DRAFT.value:
            raise ValueError("Only an owned draft search can be updated before activation")
        self.db.table("search_requirements").update(self._requirements_payload(requirements, raw_text)).eq("search_id", str(search_id)).execute()

    def get_editable_search(self, user_id: UUID) -> tuple[dict, dict]:
        """Return the newest renter-owned live search and its requirements."""
        sessions = self.db.table("search_sessions").select("*").eq("user_id", str(user_id)).order("created_at", desc=True).execute()
        session = next((item for item in (sessions.data or []) if item.get("status") in {SearchStatus.ACTIVE.value, SearchStatus.PAUSED.value}), None)
        if session is None:
            raise ValueError("You do not have an active or paused search to edit")
        requirements = self.db.table("search_requirements").select("*").eq("search_id", session["id"]).execute()
        if not requirements.data:
            raise ValueError("Your saved requirements could not be found")
        return session, requirements.data[0]

    def update_live_search(self, user_id: UUID, search_id: UUID, patch: RequirementEditResponse, raw_text: str) -> int:
        """Persist a renter-owned edit, advance version, and queue fresh matching.

        The version equality predicate detects simultaneous edits so stale jobs cannot
        silently overwrite a newer search profile.
        """
        session_result = self.db.table("search_sessions").select("*").eq("id", str(search_id)).eq("user_id", str(user_id)).execute()
        if not session_result.data:
            raise ValueError("Search was not found for this renter")
        session = session_result.data[0]
        if session.get("status") not in {SearchStatus.ACTIVE.value, SearchStatus.PAUSED.value}:
            raise ValueError("Only an active or paused search can be edited")
        requirement_result = self.db.table("search_requirements").select("*").eq("search_id", str(search_id)).execute()
        if not requirement_result.data:
            raise ValueError("Search requirements are missing")

        merged = self.merge_live_requirements(requirement_result.data[0], patch)
        missing = self._missing_persisted_core_requirements(merged)
        if missing:
            raise ValueError(f"This update would remove required search details: {', '.join(missing)}")
        current_version = int(session.get("version") or 1)
        next_version = current_version + 1
        update_result = self.db.table("search_sessions").update({"version": next_version}).eq("id", str(search_id)).eq("user_id", str(user_id)).eq("version", current_version).execute()
        if not update_result.data:
            raise RuntimeError("Your search changed elsewhere; please send the update again")
        merged["raw_requirement_text"] = f"{requirement_result.data[0].get('raw_requirement_text') or ''}\nUpdate: {raw_text}".strip()
        self.db.table("search_requirements").update(merged).eq("search_id", str(search_id)).execute()
        self._queue_match_job("SEARCH_UPDATED", search_id, next_version, "SEARCH_UPDATED")
        return next_version

    @staticmethod
    def merge_live_requirements(current: dict, patch: RequirementEditResponse) -> dict:
        """Return stored requirements overlaid with only explicitly supplied patch fields."""
        merged = dict(current)
        for field, value in patch.model_dump(exclude_none=True, exclude={"conversational_summary"}).items():
            if field == "core_preferences":
                existing = dict(merged.get("core_preferences") or {})
                existing.update({key: item.model_dump() if hasattr(item, "model_dump") else item for key, item in value.items()})
                merged[field] = existing
            elif field == "additional_preferences":
                existing = dict(merged.get("additional_preferences") or {})
                existing.update(value)
                merged[field] = existing
            else:
                merged[field] = value
        return merged

    def _queue_match_job(self, job_type: str, search_id: UUID, version: int, trigger: str) -> None:
        key = f"{job_type}:{search_id}:{version}"
        try:
            self.db.table("agent_jobs").insert({
                "job_type": job_type, "idempotency_key": key, "status": "PENDING",
                "payload": {"search_id": str(search_id), "search_version": version, "trigger": trigger},
                "run_after": self._now(),
            }).execute()
        except Exception as error:
            if "duplicate" not in str(error).lower() and "unique" not in str(error).lower():
                raise

    def _insert_requirements(self, search_id: UUID, requirements: RequirementExtractionResponse, raw_text: str) -> None:
        payload = self._requirements_payload(requirements, raw_text)
        payload["search_id"] = str(search_id)
        self.db.table("search_requirements").insert(payload).execute()

    @staticmethod
    def _requirements_payload(requirements: RequirementExtractionResponse, raw_text: str) -> dict:
        return {
            "listing_types": requirements.listing_types,
            "preferred_locations": requirements.preferred_locations,
            "acceptable_locations": requirements.acceptable_locations,
            "excluded_locations": requirements.excluded_locations,
            "work_location": requirements.work_location,
            "target_rent": requirements.target_rent or requirements.max_rent,
            "max_rent": requirements.max_rent,
            "preferred_move_in_date": requirements.preferred_move_in_date,
            "latest_move_in_date": requirements.latest_move_in_date,
            "preferred_property_configurations": requirements.preferred_property_configurations,
            "additional_preferences": requirements.additional_preferences,
            "raw_requirement_text": raw_text,
            "core_preferences": {key: value.model_dump() for key, value in requirements.core_preferences.items()},
        }

    @staticmethod
    def _missing_persisted_core_requirements(requirements: dict) -> list[str]:
        missing = []
        if not requirements.get("listing_types"):
            missing.append("listing type")
        if not (requirements.get("preferred_locations") or requirements.get("acceptable_locations")):
            missing.append("location")
        if not requirements.get("max_rent"):
            missing.append("maximum budget")
        if not (requirements.get("preferred_move_in_date") or requirements.get("latest_move_in_date")):
            missing.append("move-in timing")
        return missing

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
