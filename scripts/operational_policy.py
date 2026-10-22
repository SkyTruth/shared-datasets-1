"""Pure operational routing and bounded supervision contracts.

Adapters supply verified source facts, the episode's original failure time,
and whether the signed ownership checkpoint was verified. This module never
reads a clock, sends a message, starts recovery, or grants mutation authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from scripts.operational_retry import UNATTENDED
from scripts import deployment_revision as deployments


REGISTRY = Path(__file__).resolve().parents[1] / "catalog/alert-routing.json"
SUPERVISION_SCHEMA = "shared-datasets-operation-supervision-v1"
REPOSITORY = "SkyTruth/shared-datasets-1"
WORKFLOW = ".github/workflows/unattended-workflow-alert.yml"
SHA = re.compile(r"[0-9a-f]{40}")
IMMUTABLE_REVISION = re.compile(r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})")
CLOUD_EXECUTION_REVISION = re.compile(r"cloudrun-execution:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
CLOUD_SOURCE_REVISION = re.compile(r"cloudlog-source:[0-9a-f]{64}")


class PolicyError(ValueError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def instant(value: str) -> datetime:
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value),
            "expected an exact UTC operational timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PolicyError("invalid UTC operational timestamp") from error


def timestamp(value: datetime) -> str:
    require(value.tzinfo is not None and value.utcoffset() == timedelta(0), "expected UTC datetime")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def text(value: Any, field: str, *, maximum: int = 2000) -> str:
    require(isinstance(value, str) and 0 < len(value) <= maximum and value.strip() == value
            and not any(ord(character) < 32 for character in value), f"invalid {field}")
    return value


class Reason(StrEnum):
    HEALTHY = "healthy"
    UNCHANGED = "unchanged"
    SUPERSEDED = "superseded"
    SOURCE_NOT_READY = "source_not_ready"
    LOGS_ONLY = "logs_only"
    TRANSIENT_TRANSPORT = "transient_transport"
    PREREQUISITE_PENDING = "prerequisite_pending"
    ARTIFACT_VISIBILITY_PENDING = "artifact_visibility_pending"
    DETERMINISTIC_DEFECT = "deterministic_defect"
    PERMISSION_DENIED = "permission_denied"
    MISSING_CONFIGURATION = "missing_configuration"
    IDENTITY_DECISION_REQUIRED = "identity_decision_required"
    TRANSLATION_MAINTENANCE_REQUIRED = "translation_maintenance_required"
    PUBLICATION_RECEIPT_FAILED = "publication_receipt_failed"
    ARTIFACT_PROVENANCE_UNVERIFIED = "artifact_provenance_unverified"
    UNKNOWN = "unknown"
    SECURITY_RISK = "security_risk"
    DATA_INTEGRITY_RISK = "data_integrity_risk"
    UNAPPROVED_MUTATION = "unapproved_mutation"


class Phase(StrEnum):
    READ = "read"
    VALIDATION = "validation"
    PREPARATION = "preparation"
    MUTATION = "mutation"
    VERIFICATION = "verification"
    NOTIFICATION = "notification"
    DECISION = "decision"


class MutationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    UNCERTAIN = "uncertain"


class Recovery(StrEnum):
    NONE = "none"
    READ_ONLY_RETRY = "read_only_retry"
    BEFORE_MUTATION_LOCK_RETRY = "before_mutation_lock_retry"
    NPM_VISIBILITY = "npm_visibility"
    RECEIPT_ONLY_REPAIR = "receipt_only_repair"
    WAIT_FOR_PREREQUISITE = "wait_for_prerequisite"


class Disposition(StrEnum):
    RECORD_ONLY = "record_only"
    RECOVER = "recover"
    OWNER_HELD = "owner_held"
    CLARIFICATION_WAIT = "clarification_wait"
    ESCALATE = "escalate"
    INDEPENDENT_SECURITY = "independent_security"


class SupervisionStatus(StrEnum):
    REGISTERED = "registered"
    ACTIVE = "active"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    RELEASED = "released"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Policy:
    transient_recovery_seconds: int
    supervision_seconds: int
    check_in_seconds: int
    stable_recovery_seconds: int
    reasons: Mapping[Reason, str]


def load_policy(path: Path = REGISTRY) -> Policy:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate alert policy key")
            result[key] = value
        return result

    value = json.loads(path.read_text(), object_pairs_hook=unique)
    limits = ("transient_recovery_seconds", "supervision_seconds", "check_in_seconds", "stable_recovery_seconds")
    require(isinstance(value, dict) and set(value) == {"schema", "reasons", *limits}
            and value["schema"] == "shared-datasets-alert-routing-v1", "invalid alert policy schema")
    require(all(type(value[key]) is int and value[key] > 0 for key in limits), "invalid alert policy clock")
    # Safe readers are also packaged without this control-plane registry.
    # Reject drift here rather than giving those standalone helpers a runtime
    # dependency on repository catalog files or a silent fallback deadline.
    require(value["transient_recovery_seconds"] == UNATTENDED.deadline_seconds,
            "alert policy and unattended worker retry budget must agree")
    require(value["check_in_seconds"] <= value["transient_recovery_seconds"] <= value["supervision_seconds"],
            "invalid alert policy clock ordering")
    reasons = value["reasons"]
    require(isinstance(reasons, dict) and set(reasons) == set(Reason), "alert policy must classify every supported reason")
    require(all(category in {"record_only", "safe_recovery", "owner_action", "independent_security"}
                for category in reasons.values()), "unsupported alert policy reason disposition")
    require(all(reasons[reason] == "independent_security" for reason in (
        Reason.SECURITY_RISK, Reason.DATA_INTEGRITY_RISK, Reason.UNAPPROVED_MUTATION)), "security reasons require independent delivery")
    require(reasons[Reason.LOGS_ONLY] == "record_only", "transport failures must retain their reviewed logs-only route")
    return Policy(**{key: value[key] for key in limits}, reasons=MappingProxyType({Reason(key): item for key, item in reasons.items()}))


DEFAULT_POLICY = load_policy()


@dataclass(frozen=True)
class OperationBinding:
    operation_id: str
    target: str
    revision: str
    pr_number: int | None

    def __post_init__(self):
        require(isinstance(self.operation_id, str) and re.fullmatch(r"[a-z][a-zA-Z0-9:._/-]{0,199}", self.operation_id),
                "invalid operation identity")
        require(isinstance(self.target, str) and re.fullmatch(r"[a-z][a-zA-Z0-9:._/-]{0,199}", self.target), "invalid operation target")
        require(isinstance(self.revision, str), "invalid exact operation revision")
        execution = CLOUD_EXECUTION_REVISION.fullmatch(self.revision)
        require(IMMUTABLE_REVISION.fullmatch(self.revision) or CLOUD_SOURCE_REVISION.fullmatch(self.revision)
                or execution and UUID(execution[1]).int != 0,
                "invalid exact operation revision")
        require(self.pr_number is None or type(self.pr_number) is int and self.pr_number > 0, "invalid operation PR")

    @classmethod
    def from_dict(cls, value: dict) -> OperationBinding:
        require(isinstance(value, dict) and set(value) == set(cls.__dataclass_fields__), "invalid operation binding schema")
        return cls(**value)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OperationalObservation:
    operation_id: str
    target: str
    revision: str
    pr_number: int | None
    source_id: str
    observed_at: str
    phase: Phase
    reason: Reason
    mutation_status: MutationStatus
    recovery: Recovery
    required_action: str
    impact: str
    published_data_state: str
    evidence: tuple[str, ...]

    def __post_init__(self):
        self.binding
        text(self.source_id, "verified source identity", maximum=500)
        instant(self.observed_at)
        for field, kind in (("phase", Phase), ("reason", Reason), ("mutation_status", MutationStatus), ("recovery", Recovery)):
            require(isinstance(getattr(self, field), kind), f"invalid observation {field}")
        for field in ("required_action", "impact", "published_data_state"):
            text(getattr(self, field), field)
        require(isinstance(self.evidence, tuple) and 0 < len(self.evidence) <= 32, "missing bounded operational evidence")
        for reference in self.evidence:
            text(reference, "operational evidence", maximum=2000)
        if CLOUD_EXECUTION_REVISION.fullmatch(self.revision) or CLOUD_SOURCE_REVISION.fullmatch(self.revision):
            require(self.reason in {Reason.ARTIFACT_PROVENANCE_UNVERIFIED, Reason.LOGS_ONLY},
                    "execution identity cannot establish artifact provenance")
        if self.reason == Reason.LOGS_ONLY:
            cloud_scope = self.operation_id in deployments.LOGS_ONLY_CLOUD_SCOPES
            github_scope = (
                self.operation_id == "deployment:" + self.target and self.target in deployments.LOGS_ONLY_TARGETS
                or self.operation_id == self.target
                and self.target in deployments.LOGS_ONLY_WORKFLOW_SCOPES
            )
            require(self.recovery == Recovery.NONE
                    and (cloud_scope and self.target == self.operation_id
                         and re.fullmatch(r"cloud(?:api|exec|log):[0-9a-f]{64}", self.source_id)
                         or github_scope and SHA.fullmatch(self.revision)
                         and re.fullmatch(r"github:[1-9][0-9]*:[1-9][0-9]*:[0-9]+:[0-9]+", self.source_id)),
                    "logs-only routing requires an exact registered transport source and cannot authorize replay")
        if self.reason == Reason.ARTIFACT_PROVENANCE_UNVERIFIED:
            require(self.mutation_status == MutationStatus.UNCERTAIN and self.recovery == Recovery.NONE,
                    "unverified artifact provenance must freeze replay")
        if self.reason in {Reason.HEALTHY, Reason.UNCHANGED, Reason.SUPERSEDED, Reason.SOURCE_NOT_READY}:
            require(self.mutation_status != MutationStatus.UNCERTAIN and self.recovery == Recovery.NONE,
                    "a quiet outcome cannot conceal uncertain mutation or request replay")

    @property
    def binding(self) -> OperationBinding:
        return OperationBinding(self.operation_id, self.target, self.revision, self.pr_number)

    @classmethod
    def from_dict(cls, value: dict) -> OperationalObservation:
        require(isinstance(value, dict) and set(value) == set(cls.__dataclass_fields__), "invalid operational observation schema")
        value = dict(value)
        require(isinstance(value["evidence"], list), "operational evidence must be a JSON array")
        value["evidence"] = tuple(value["evidence"])
        try:
            for field, kind in (("phase", Phase), ("reason", Reason), ("mutation_status", MutationStatus), ("recovery", Recovery)):
                value[field] = kind(value[field])
        except (ValueError, TypeError) as error:
            raise PolicyError("unsupported explicit observation classification") from error
        return cls(**value)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        return value


@dataclass(frozen=True)
class Decision:
    """Routing facts, never recovery or deployment authorization.

    retry_safe describes the verified recovery path's safety. It does not
    replenish a worker's retry budget; RECOVER identifies the quiet recovery
    window, and source workers still enforce their own deadline and authority.
    """
    disposition: Disposition
    eligible_at: str | None
    retry_safe: bool
    freeze_replay: bool
    owner: str | None
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


AUTHORITY_FIELDS = {"actor", "repository", "workflow", "ref", "executor_sha", "run_id", "attempt"}
SUPERVISION_FIELDS = {"schema", *OperationBinding.__dataclass_fields__, "responsible_human", "conversation_ref",
                      "registered_at", "updated_at", "first_failure_at", "deadline", "status", "last_check_in",
                      "current_action", "question_ref", "handoff_ref", "authority", "covered_bindings"}


def validate_authority(value: dict, *, responsible_human: str) -> dict:
    """Validate metadata obtained by the authenticated workflow adapter.

    This shape check does not verify an attestation. The caller must authenticate
    the run and verify persisted ownership signatures before using a hold.
    """
    require(isinstance(value, dict) and set(value) == AUTHORITY_FIELDS, "invalid ownership authority schema")
    require(isinstance(value["actor"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", value["actor"])
            and value["actor"] == responsible_human, "responsible human differs from authenticated actor")
    require(value["repository"] == REPOSITORY and value["workflow"] == WORKFLOW and value["ref"] == "refs/heads/main",
            "ownership requires the trusted main notification workflow")
    require(isinstance(value["executor_sha"], str) and SHA.fullmatch(value["executor_sha"]), "invalid ownership executor revision")
    require(all(type(value[key]) is int and value[key] > 0 for key in ("run_id", "attempt")), "invalid ownership authority attempt")
    return dict(value)


def conversation(value: str) -> str:
    text(value, "originating conversation", maximum=2000)
    parsed = urlsplit(value)
    require(parsed.scheme in {"https", "codex", "claude"} and parsed.netloc and parsed.path and not parsed.fragment,
            "originating conversation must identify a concrete chat")
    return value


def question_in_conversation(reference: str, origin: str) -> bool:
    return isinstance(reference, str) and any(reference.startswith(origin + suffix) and len(reference) > len(origin + suffix)
                                             for suffix in ("#", "/messages/"))


def validate_supervision_request(value: dict) -> dict:
    """Parse untrusted CLI/dispatch inputs without accepting clocks or proof."""
    require(isinstance(value, dict) and set(value) == {
        "command", "binding", "responsible_human", "conversation_ref", "current_action", "question_ref", "handoff_ref",
    }, "invalid supervision request schema")
    require(value["command"] in {"claim", "check_in", "clarification", "release", "supersede"}, "unsupported supervision command")
    binding = OperationBinding.from_dict(value["binding"])
    require(IMMUTABLE_REVISION.fullmatch(binding.revision), "ownership requires a verified immutable artifact revision")
    require(isinstance(value["responsible_human"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", value["responsible_human"]),
            "invalid responsible human")
    conversation(value["conversation_ref"])
    text(value["current_action"], "current supervised action")
    if value["command"] == "clarification":
        text(value["question_ref"], "clarification question reference")
        require(question_in_conversation(value["question_ref"], value["conversation_ref"]),
                "clarification must reference the question in its originating chat")
    else:
        require(value["question_ref"] is None, "question reference requires a clarification command")
    if value["handoff_ref"] is not None:
        text(value["handoff_ref"], "explicit handoff reference")
    return {**value, "binding": dict(value["binding"])}


def validate_supervision(value: dict, *, binding: OperationBinding | None = None,
                         first_failure_at: str | None = None, policy: Policy = DEFAULT_POLICY) -> dict:
    require(isinstance(value, dict) and set(value) == SUPERVISION_FIELDS and value["schema"] == SUPERVISION_SCHEMA,
            "invalid supervision record schema")
    actual_binding = OperationBinding.from_dict({key: value[key] for key in OperationBinding.__dataclass_fields__})
    require(IMMUTABLE_REVISION.fullmatch(actual_binding.revision), "ownership requires a verified immutable artifact revision")
    covered = value["covered_bindings"]
    require(isinstance(covered, list) and len(covered) <= 64, "invalid supervised source binding history")
    historical = [OperationBinding.from_dict(item) for item in covered]
    require(len(historical) == len(set(historical)) and actual_binding not in historical
            and all(item.operation_id == actual_binding.operation_id and item.target == actual_binding.target
                    and IMMUTABLE_REVISION.fullmatch(item.revision) for item in historical),
            "supervision history differs from the verified operation/target")
    require(binding is None or binding in [actual_binding, *historical], "supervision does not bind the exact operation/PR/revision/target")
    validate_authority(value["authority"], responsible_human=value["responsible_human"])
    conversation(value["conversation_ref"])
    registered, updated = instant(value["registered_at"]), instant(value["updated_at"])
    require(registered <= updated, "supervision update precedes registration")
    text(value["current_action"], "current supervised action")
    try:
        status = SupervisionStatus(value["status"])
    except (TypeError, ValueError) as error:
        raise PolicyError("invalid supervision status") from error
    failure, deadline = value["first_failure_at"], value["deadline"]
    require((failure is None) == (deadline is None), "supervision failure clock is incomplete")
    if failure is not None:
        require(instant(failure) <= updated and instant(deadline) == instant(failure) + timedelta(seconds=policy.supervision_seconds),
                "supervision deadline differs from the original failure clock")
        require(first_failure_at is None or instant(failure) <= instant(first_failure_at), "supervision restarted the failure clock")
    if first_failure_at is not None:
        instant(first_failure_at)
    if value["last_check_in"] is not None:
        require(registered <= instant(value["last_check_in"]) <= updated, "invalid supervision check-in clock")
    if status == SupervisionStatus.REGISTERED:
        require(value["last_check_in"] is None and value["question_ref"] is None, "registration is not active supervision")
    elif status == SupervisionStatus.ACTIVE:
        require(value["last_check_in"] is not None and value["question_ref"] is None, "active supervision requires an explicit check-in")
    elif status == SupervisionStatus.AWAITING_CLARIFICATION:
        text(value["question_ref"], "clarification question reference")
        require(question_in_conversation(value["question_ref"], value["conversation_ref"]), "clarification must reference the question in its originating chat")
    else:
        require(value["question_ref"] is None, "released supervision cannot retain a clarification wait")
    if value["handoff_ref"] is not None:
        text(value["handoff_ref"], "explicit handoff reference")
    return {**value, "authority": dict(value["authority"]), "covered_bindings": [dict(item) for item in covered]}


def covers_binding(value: dict, binding: OperationBinding) -> bool:
    """Match current work or a covered source derived from verified history."""
    record = validate_supervision(value)
    current = OperationBinding.from_dict({key: record[key] for key in OperationBinding.__dataclass_fields__})
    return binding == current or binding.to_dict() in record["covered_bindings"]


def bind_supervision(value: dict, binding: OperationBinding, *, first_failure_at: str, now: str,
                     policy: Policy = DEFAULT_POLICY) -> dict:
    """Bind a prospective claim only to authoritative original failure evidence."""
    record = validate_supervision(value, binding=binding, policy=policy)
    observed, current = instant(first_failure_at), instant(now)
    require(observed <= current and instant(record["updated_at"]) <= current, "invalid verified failure binding time")
    if record["first_failure_at"] is not None:
        observed = min(observed, instant(record["first_failure_at"]))
    record.update(first_failure_at=timestamp(observed), deadline=timestamp(observed + timedelta(seconds=policy.supervision_seconds)), updated_at=now)
    return validate_supervision(record, binding=binding, first_failure_at=first_failure_at, policy=policy)


def transition_supervision(previous: dict | None, command: str, binding: OperationBinding, *, now: str,
                           authority: dict, responsible_human: str, conversation_ref: str, current_action: str,
                           question_ref: str | None = None, handoff_ref: str | None = None,
                           verified_bindings: tuple[OperationBinding, ...] | list[OperationBinding] = (),
                           policy: Policy = DEFAULT_POLICY) -> dict:
    require(command in {"claim", "check_in", "clarification", "release", "supersede"}, "unsupported supervision command")
    require(IMMUTABLE_REVISION.fullmatch(binding.revision), "ownership requires a verified immutable artifact revision")
    require(isinstance(verified_bindings, (tuple, list)) and all(isinstance(item, OperationBinding) for item in verified_bindings),
            "coverage requires exact source bindings verified by the ledger adapter")
    current = instant(now)
    authority = validate_authority(authority, responsible_human=responsible_human)
    conversation(conversation_ref)
    text(current_action, "current supervised action")
    require(command == "clarification" or question_ref is None, "question reference requires a clarification command")
    if previous is None:
        require(command == "claim", "supervision must be registered before a transition")
        record = {"schema": SUPERVISION_SCHEMA, **binding.to_dict(), "responsible_human": responsible_human,
                  "conversation_ref": conversation_ref, "registered_at": now, "updated_at": now,
                  "first_failure_at": None, "deadline": None, "status": SupervisionStatus.REGISTERED.value,
                  "last_check_in": None, "current_action": current_action, "question_ref": None,
                  "handoff_ref": handoff_ref, "authority": authority, "covered_bindings": []}
    else:
        record = validate_supervision(previous, policy=policy)
        previous_binding = OperationBinding.from_dict({key: record[key] for key in OperationBinding.__dataclass_fields__})
        require(current >= instant(record["updated_at"]), "supervision transition moved backwards")
        require(all(record[key] == getattr(binding, key) for key in ("operation_id", "target")),
                "supervision cannot transfer to another operation/target")
        require(record["responsible_human"] == responsible_human, "supervision cannot transfer human responsibility")
        require(record["conversation_ref"] == conversation_ref, "supervision remains in its originating conversation")
        require((record["revision"], record["pr_number"]) == (binding.revision, binding.pr_number) or command == "claim",
                "new revisions or PRs require explicit registration")
        if command not in {"release", "supersede"} and record["deadline"] is not None:
            require(current < instant(record["deadline"]), "original supervision deadline has expired")
        if command in {"check_in", "clarification"}:
            require(record["status"] not in {SupervisionStatus.RELEASED, SupervisionStatus.SUPERSEDED},
                    "released ownership requires explicit registration")
        record.update(**binding.to_dict(), updated_at=now, authority=authority, current_action=current_action, handoff_ref=handoff_ref)
        if command == "claim":
            covered = [OperationBinding.from_dict(item) for item in record["covered_bindings"]]
            # Only the signed-ledger adapter can supply verified source facts.
            # Prospective guessed revisions do not become covered failures.
            verified_previous = previous_binding in verified_bindings
            if previous_binding != binding and verified_previous and previous_binding not in covered:
                covered.append(previous_binding)
            covered = [item for item in covered if item != binding]
            record["covered_bindings"] = [item.to_dict() for item in covered]
            continuity = verified_previous or bool(covered)
            if not continuity or record["status"] not in {SupervisionStatus.ACTIVE, SupervisionStatus.AWAITING_CLARIFICATION}:
                record.update(status=SupervisionStatus.REGISTERED.value, last_check_in=None, question_ref=None)
    if command == "check_in":
        record.update(status=SupervisionStatus.ACTIVE.value, last_check_in=now, question_ref=None)
    elif command == "clarification":
        require(question_in_conversation(question_ref, conversation_ref), "clarification must reference the question in its originating chat")
        record.update(status=SupervisionStatus.AWAITING_CLARIFICATION.value, question_ref=question_ref)
    elif command in {"release", "supersede"}:
        record.update(status=SupervisionStatus.RELEASED.value if command == "release" else SupervisionStatus.SUPERSEDED.value, question_ref=None)
    return validate_supervision(record, binding=binding, policy=policy)


def safe_recovery(observation: OperationalObservation) -> bool:
    status, recovery = observation.mutation_status, observation.recovery
    if recovery == Recovery.BEFORE_MUTATION_LOCK_RETRY:
        return status == MutationStatus.NOT_STARTED
    if recovery in {Recovery.RECEIPT_ONLY_REPAIR, Recovery.NPM_VISIBILITY}:
        return status == MutationStatus.COMPLETED
    if recovery == Recovery.WAIT_FOR_PREREQUISITE:
        return status in {MutationStatus.NOT_REQUIRED, MutationStatus.NOT_STARTED}
    return recovery == Recovery.READ_ONLY_RETRY and status in {
        MutationStatus.NOT_REQUIRED, MutationStatus.NOT_STARTED, MutationStatus.COMPLETED,
    }


def decide(observation: OperationalObservation, *, first_failure_at: str, now: str,
           supervision: dict | None = None, ownership_verified: bool = False,
           public_parent_exists: bool = False, policy: Policy = DEFAULT_POLICY) -> Decision:
    require(isinstance(observation, OperationalObservation), "decision requires a validated observation")
    require(type(ownership_verified) is bool and type(public_parent_exists) is bool, "invalid verified policy facts")
    failure, current = instant(first_failure_at), instant(now)
    require(failure <= instant(observation.observed_at) <= current, "invalid original failure/observation clock")
    category = policy.reasons[observation.reason]
    if category == "independent_security":
        return Decision(Disposition.INDEPENDENT_SECURITY, first_failure_at, False, True, None, "security_or_integrity_requires_independent_delivery")
    if observation.reason == Reason.ARTIFACT_PROVENANCE_UNVERIFIED:
        return Decision(Disposition.ESCALATE, first_failure_at, False, True, None, "artifact_provenance_unverified")
    freeze_replay = observation.mutation_status == MutationStatus.UNCERTAIN
    if category == "record_only":
        return Decision(Disposition.RECORD_ONLY, None, False, freeze_replay, None, observation.reason.value)
    retry_safe = category == "safe_recovery" and safe_recovery(observation)
    # Existing public parents remain public; a late claim cannot retract an
    # escalation or create a second parent. Delivery/update is ledger-owned.
    if public_parent_exists:
        return Decision(Disposition.ESCALATE, first_failure_at, retry_safe, freeze_replay, None, "update_existing_actionable_parent")
    if supervision is not None and ownership_verified:
        record = validate_supervision(supervision, binding=observation.binding, first_failure_at=first_failure_at, policy=policy)
        require(instant(record["updated_at"]) <= current, "supervision is from the future")
        if record["first_failure_at"] is not None:
            deadline = min(instant(record["deadline"]), failure + timedelta(seconds=policy.supervision_seconds))
            owner = record["responsible_human"]
            if current >= deadline:
                return Decision(Disposition.ESCALATE, timestamp(deadline), retry_safe, freeze_replay, owner, "supervision_deadline_expired")
            if record["status"] == SupervisionStatus.AWAITING_CLARIFICATION:
                return Decision(Disposition.CLARIFICATION_WAIT, timestamp(deadline), retry_safe, freeze_replay, owner, "awaiting_recorded_human_clarification")
            if record["status"] == SupervisionStatus.ACTIVE:
                check_in_due = instant(record["last_check_in"]) + timedelta(seconds=policy.check_in_seconds)
                if current < check_in_due:
                    return Decision(Disposition.OWNER_HELD, timestamp(min(check_in_due, deadline)), retry_safe, freeze_replay, owner, "active_accountable_recovery")
                return Decision(Disposition.ESCALATE, timestamp(check_in_due), retry_safe, freeze_replay, owner, "supervision_check_in_stale")
            if record["status"] in {SupervisionStatus.RELEASED, SupervisionStatus.SUPERSEDED}:
                return Decision(Disposition.ESCALATE, record["updated_at"], retry_safe, freeze_replay, owner, "supervision_released_or_superseded")
    if freeze_replay:
        return Decision(Disposition.ESCALATE, first_failure_at, False, True, None, "inspect_authoritative_mutation_state_before_replay")
    deadline = failure + timedelta(seconds=policy.transient_recovery_seconds)
    if retry_safe and current < deadline:
        return Decision(Disposition.RECOVER, timestamp(deadline), True, False, None, "bounded_safe_source_recovery")
    return Decision(Disposition.ESCALATE, timestamp(deadline) if retry_safe else first_failure_at,
                    retry_safe, False, None, "safe_recovery_exhausted" if retry_safe else "owner_action_required")
