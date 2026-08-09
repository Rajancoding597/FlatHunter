"""Deterministic listing-to-search matching for FlatHunter V0.

This module deliberately keeps matching decisions independent from Telegram, LLMs,
and database writes. Unknown listing facts are qualification gaps, never negatives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.common.enums import MatchStatus

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "location": 30,
    "budget": 25,
    "move_in": 15,
    "property": 10,
    "amenities": 10,
    "financial_terms": 10,
}
SUPPORTED_LISTING_TYPES = {"ENTIRE_PROPERTY", "PRIVATE_ROOM", "SHARED_ROOM"}
UNKNOWN_LOCATION_VALUES = {"", "unknown", "n/a", "na", "none"}
STRONG_MATCH_MIN_FIT = 85.0
STRONG_MATCH_MIN_COMPLETENESS = 70.0


@dataclass
class MatchEvaluation:
    status: MatchStatus
    fit_score: float = 0.0
    information_completeness: float = 0.0
    hard_rejection_reasons: List[Dict[str, Any]] = field(default_factory=list)
    positive_reasons: List[str] = field(default_factory=list)
    missing_information: List[Dict[str, Any]] = field(default_factory=list)
    soft_context_evaluation: Dict[str, Any] = field(default_factory=dict)
    score_breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)


class MatchingEngine:
    """Evaluate a listing against a normalized renter requirement profile."""

    def __init__(self, db: Optional[Any] = None):
        # Delayed construction makes pure evaluation testable without Supabase.
        if db is None:
            from app.db.client import get_supabase_client
            db = get_supabase_client()
        self.db = db

    def evaluate_match(self, search_req: Dict[str, Any], listing: Dict[str, Any]) -> MatchEvaluation:
        result = MatchEvaluation(status=MatchStatus.POSSIBLE_MATCH)
        preferences = search_req.get("core_preferences") or {}
        listing_type = listing.get("listing_type")
        locality = self._known_location(listing.get("locality"))
        city = self._known_location(listing.get("city"))
        rent = listing.get("rent")
        availability = listing.get("availability_status") or "UNKNOWN"
        max_rent = search_req.get("max_rent")
        target_rent = search_req.get("target_rent") or max_rent
        monthly_cost = self._known_monthly_cost(listing)

        # Explicit hard contradictions only.
        preferred_types = set(search_req.get("listing_types") or [])
        if listing_type and listing_type not in SUPPORTED_LISTING_TYPES:
            self._reject(result, "UNSUPPORTED_LISTING_TYPE", "listing_type", listing_type)
        elif preferred_types and listing_type and listing_type not in preferred_types:
            self._reject(result, "LISTING_TYPE_MISMATCH", "listing_type", listing_type)

        preferred_locations = self._normalised_locations(search_req.get("preferred_locations"))
        acceptable_locations = self._normalised_locations(search_req.get("acceptable_locations"))
        excluded_locations = self._normalised_locations(search_req.get("excluded_locations"))
        location_text = " ".join(value for value in (locality, city) if value).lower()
        if location_text:
            if any(location in location_text for location in excluded_locations):
                self._reject(result, "EXCLUDED_LOCALITY", "locality", locality or city)
            elif preferred_locations or acceptable_locations:
                allowed_locations = preferred_locations | acceptable_locations
                if not any(location in location_text for location in allowed_locations):
                    self._reject(result, "LOCALITY_NOT_ALLOWED", "locality", locality or city)

        if max_rent is not None and monthly_cost is not None and monthly_cost > max_rent:
            self._reject(result, "MAX_MONTHLY_BUDGET_EXCEEDED", "monthly_cost", monthly_cost)
        if availability == "UNAVAILABLE":
            self._reject(result, "LISTING_UNAVAILABLE", "availability_status", availability)

        latest_move_in = search_req.get("latest_move_in_date")
        available_from = listing.get("available_from")
        if latest_move_in and available_from and str(available_from) > str(latest_move_in):
            self._reject(result, "MOVE_IN_DEADLINE_MISSED", "available_from", str(available_from))
        self._evaluate_required_preferences(result, preferences, listing)

        # Completeness and qualification gaps are renter-specific.
        completeness_total = 0.0
        completeness_known = 0.0

        def completeness(field: str, known: bool, weight: float, importance: str, priority: int, question_intent: str):
            nonlocal completeness_total, completeness_known
            completeness_total += weight
            if known:
                completeness_known += weight
            else:
                self._add_gap(result, field, importance, priority, question_intent)

        completeness("availability_status", availability == "AVAILABLE", 3, "CORE", 1, "CONFIRM_AVAILABILITY")
        completeness("listing_type", listing_type is not None, 3, "CORE", 2, "CONFIRM_LISTING_TYPE")
        completeness("locality", bool(locality), 3, "CORE", 2, "CONFIRM_LOCATION")
        completeness("rent", rent is not None, 3, "CORE", 2, "CONFIRM_RENT")
        if search_req.get("preferred_move_in_date") or latest_move_in:
            completeness("available_from", available_from is not None, 2, "CORE", 3, "CONFIRM_MOVE_IN")
        if max_rent is not None and listing.get("maintenance") is None:
            completeness("maintenance", False, 3, "CORE", 3, "CONFIRM_MAINTENANCE")

        for preference_key, preference in preferences.items():
            importance = self._preference_importance(preference)
            if importance == "DOES_NOT_MATTER":
                continue
            field_name = self._listing_field_for_preference(preference_key)
            known = self._preference_listing_value(preference_key, listing) is not None
            if importance == "REQUIRED":
                completeness(field_name, known, 3, "REQUIRED", 2, self._question_intent(field_name))
            else:
                completeness(field_name, known, 1, "PREFERRED", 4, self._question_intent(field_name))

        result.information_completeness = round(100 * completeness_known / completeness_total if completeness_total else 100.0, 2)

        # Fit score uses known applicable evidence only.
        weighted_score = 0.0
        weight_total = 0.0

        def score(category: str, fraction: Optional[float], label: str):
            nonlocal weighted_score, weight_total
            if fraction is None:
                return
            weight = DEFAULT_WEIGHTS[category]
            weighted_score += weight * fraction
            weight_total += weight
            result.score_breakdown[category] = {"weight": weight, "fraction": round(fraction, 4)}
            if fraction >= 0.8:
                result.positive_reasons.append(label)

        if location_text and (preferred_locations or acceptable_locations):
            if any(location in location_text for location in preferred_locations):
                score("location", 1.0, "Preferred location")
            elif any(location in location_text for location in acceptable_locations):
                score("location", 0.7, "Acceptable location")

        if monthly_cost is not None and target_rent is not None and max_rent is not None:
            if monthly_cost <= target_rent:
                score("budget", 1.0, "Within target budget")
            elif monthly_cost <= max_rent:
                if max_rent == target_rent:
                    score("budget", 1.0, "Within maximum budget")
                else:
                    fraction = 1.0 - 0.5 * ((monthly_cost - target_rent) / (max_rent - target_rent))
                    score("budget", max(0.5, fraction), "Within maximum budget")

        preferred_move_in = search_req.get("preferred_move_in_date")
        if available_from:
            if preferred_move_in and str(available_from) <= str(preferred_move_in):
                score("move_in", 1.0, "Available by preferred move-in")
            elif latest_move_in and str(available_from) <= str(latest_move_in):
                score("move_in", 0.6, "Available within move-in window")
            elif not latest_move_in:
                score("move_in", 1.0, "Move-in date available")

        preferred_configurations = search_req.get("preferred_property_configurations") or []
        listing_configuration = listing.get("property_configuration")
        if preferred_configurations and listing_configuration is not None:
            score("property", 1.0 if listing_configuration in preferred_configurations else 0.0, "Preferred property configuration")

        amenity_fractions = []
        for key, preference in preferences.items():
            if self._preference_importance(preference) == "DOES_NOT_MATTER" or self._is_financial_preference(key):
                continue
            listing_value = self._preference_listing_value(key, listing)
            if listing_value is not None:
                amenity_fractions.append(1.0 if self._values_match(self._preference_value(preference), listing_value) else 0.0)
        if amenity_fractions:
            score("amenities", sum(amenity_fractions) / len(amenity_fractions), "Preferred amenities matched")

        brokerage_preference = preferences.get("no_brokerage") or preferences.get("brokerage")
        brokerage = listing.get("brokerage")
        if brokerage_preference is not None and brokerage is not None:
            avoid_brokerage = self._preference_value(brokerage_preference) in (True, "AVOID", "NO_BROKERAGE")
            score("financial_terms", 1.0 if brokerage == 0 else (0.2 if avoid_brokerage else 0.0), "No brokerage")

        result.fit_score = round(100 * weighted_score / weight_total if weight_total else 0.0, 2)

        # Classification happens only after every deterministic fact is evaluated.
        if result.hard_rejection_reasons:
            result.status = MatchStatus.REJECTED
        else:
            core_evidence = listing_type is not None and bool(locality) and rent is not None
            important_gaps = any(gap["importance"] in {"CORE", "REQUIRED"} for gap in result.missing_information)
            if not core_evidence:
                result.status = MatchStatus.NEEDS_QUALIFICATION
            elif result.fit_score >= STRONG_MATCH_MIN_FIT:
                if availability == "AVAILABLE" and not important_gaps and result.information_completeness >= STRONG_MATCH_MIN_COMPLETENESS:
                    result.status = MatchStatus.STRONG_MATCH
                else:
                    result.status = MatchStatus.NEEDS_QUALIFICATION
            else:
                result.status = MatchStatus.POSSIBLE_MATCH
        return result

    @staticmethod
    def _known_location(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return None if text.lower() in UNKNOWN_LOCATION_VALUES else text

    @staticmethod
    def _normalised_locations(values: Any) -> set[str]:
        return {str(value).strip().lower() for value in (values or []) if str(value).strip()}

    @staticmethod
    def _known_monthly_cost(listing: Dict[str, Any]) -> Optional[float]:
        rent = listing.get("rent")
        if rent is None:
            return None
        maintenance = listing.get("maintenance")
        if maintenance is not None and listing.get("maintenance_mandatory") is True:
            return float(rent) + float(maintenance)
        return float(rent)

    def _evaluate_required_preferences(self, result: MatchEvaluation, preferences: Dict[str, Any], listing: Dict[str, Any]) -> None:
        for key, preference in preferences.items():
            if self._preference_importance(preference) != "REQUIRED":
                continue
            listing_value = self._preference_listing_value(key, listing)
            if listing_value is not None and not self._values_match(self._preference_value(preference), listing_value):
                self._reject(result, "REQUIRED_PREFERENCE_MISMATCH", self._listing_field_for_preference(key), listing_value)

    @staticmethod
    def _preference_importance(preference: Any) -> str:
        return str(preference.get("importance", "PREFERRED")) if isinstance(preference, dict) else "PREFERRED"

    @staticmethod
    def _preference_value(preference: Any) -> Any:
        return preference.get("value") if isinstance(preference, dict) else preference

    @staticmethod
    def _listing_field_for_preference(key: str) -> str:
        return "brokerage" if key == "no_brokerage" else key

    @staticmethod
    def _is_financial_preference(key: str) -> bool:
        return key in {"no_brokerage", "brokerage", "deposit", "max_deposit", "max_brokerage"}

    def _preference_listing_value(self, key: str, listing: Dict[str, Any]) -> Any:
        if key == "no_brokerage":
            brokerage = listing.get("brokerage")
            return None if brokerage is None else brokerage == 0
        return listing.get(key)

    @staticmethod
    def _values_match(desired: Any, actual: Any) -> bool:
        if isinstance(desired, str) and isinstance(actual, str):
            return desired.strip().upper() == actual.strip().upper()
        return desired == actual

    @staticmethod
    def _question_intent(field: str) -> str:
        return f"CONFIRM_{field.upper()}"

    @staticmethod
    def _reject(result: MatchEvaluation, code: str, field: str, listing_value: Any) -> None:
        result.hard_rejection_reasons.append({"code": code, "field": field, "listing_value": listing_value})

    @staticmethod
    def _add_gap(result: MatchEvaluation, field: str, importance: str, priority: int, question_intent: str) -> None:
        if any(gap["field"] == field for gap in result.missing_information):
            return
        result.missing_information.append({"field": field, "importance": importance, "priority": priority, "question_intent": question_intent})

    def process_new_listing(self, listing_id: str) -> None:
        listing_result = self.db.table("listings").select("*").eq("id", listing_id).execute()
        if not listing_result.data:
            return
        listing = listing_result.data[0]
        if listing.get("availability_status") == "UNAVAILABLE":
            return
        active_searches = self.db.table("search_sessions").select("id,city,version").eq("status", "ACTIVE").execute()
        for search in active_searches.data:
            if search.get("city") and listing.get("city") and search["city"].lower() != listing["city"].lower():
                continue
            requirement_result = self.db.table("search_requirements").select("*").eq("search_id", search["id"]).execute()
            if requirement_result.data:
                self._upsert_match(search["id"], listing_id, self.evaluate_match(requirement_result.data[0], listing), search_version=search.get("version"), listing_version=listing.get("version"))

    def process_new_search(self, search_id: str) -> None:
        session_result = self.db.table("search_sessions").select("id,status,version").eq("id", search_id).execute()
        if not session_result.data or session_result.data[0].get("status") != "ACTIVE":
            return
        search = session_result.data[0]
        requirement_result = self.db.table("search_requirements").select("*").eq("search_id", search_id).execute()
        if not requirement_result.data:
            return
        listings_result = self.db.table("listings").select("*").neq("availability_status", "UNAVAILABLE").execute()
        for listing in listings_result.data:
            self._upsert_match(
                search_id,
                listing["id"],
                self.evaluate_match(requirement_result.data[0], listing),
                search_version=search.get("version"),
                listing_version=listing.get("version"),
            )
        self._queue_initial_results(search_id, int(search.get("version") or 1))

    def _queue_initial_results(self, search_id: str, version: int) -> None:
        """One durable initial-results summary per processed search version."""
        key = f"SEND_INITIAL_RESULTS:{search_id}:{version}"
        try:
            self.db.table("agent_jobs").insert({
                "job_type": "SEND_INITIAL_RESULTS",
                "idempotency_key": key,
                "status": "PENDING",
                "payload": {"search_id": search_id, "search_version": version},
                "run_after": "now()",
            }).execute()
        except Exception as error:
            if "duplicate" not in str(error).lower() and "unique" not in str(error).lower():
                raise

    def _upsert_match(self, search_id: str, listing_id: str, evaluation: MatchEvaluation, search_version: Optional[int] = None, listing_version: Optional[int] = None) -> None:
        existing = self.db.table("matches").select("status").eq("search_id", search_id).eq("listing_id", listing_id).execute()
        previous_status = existing.data[0]["status"] if existing.data else None
        self.db.table("matches").upsert({
            "search_id": search_id,
            "listing_id": listing_id,
            "status": evaluation.status.value,
            "fit_score": evaluation.fit_score,
            "information_completeness": evaluation.information_completeness,
            "hard_rejection_reasons": evaluation.hard_rejection_reasons,
            "positive_reasons": evaluation.positive_reasons,
            "missing_information": evaluation.missing_information,
            "soft_context_evaluation": evaluation.soft_context_evaluation,
            "score_breakdown": evaluation.score_breakdown,
            "search_version": search_version,
            "listing_version": listing_version,
        }, on_conflict="search_id,listing_id").execute()
        if evaluation.status == MatchStatus.STRONG_MATCH and previous_status != MatchStatus.STRONG_MATCH.value:
            self.db.table("agent_jobs").insert({
                "job_type": "SEND_RENTER_NOTIFICATION",
                "status": "PENDING",
                "payload": {"search_id": search_id, "listing_id": listing_id},
                "run_after": "now()",
            }).execute()