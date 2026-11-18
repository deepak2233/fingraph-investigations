"""SQL ledger for idempotent intake, durable cases, reviews, and an outbox.

A result, its audit event, and pending graph projection commit together. Workers
may repeat projection after a crash. A deterministic named graph makes that safe.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

metadata = MetaData()
events = Table(
    "intake_events",
    metadata,
    Column("id", String(200), primary_key=True),
    Column("tenant", String(96), nullable=False),
    Column("event_id", String(96), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("tenant", "event_id"),
)
cases = Table(
    "investigations",
    metadata,
    Column("id", String(200), primary_key=True),
    Column("tenant", String(96), nullable=False, index=True),
    Column("event_key", String(200), nullable=False, unique=True),
    Column("status", String(40), nullable=False),
    Column("version", Integer, nullable=False),
    Column("result", Text, nullable=False),
)
audit = Table(
    "audit_events",
    metadata,
    Column("id", String(250), primary_key=True),
    Column("case_id", String(200), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("case_id", "version"),
)
reviews = Table(
    "review_requests",
    metadata,
    Column("id", String(250), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("response", Text, nullable=False),
)
outbox = Table(
    "outbox",
    metadata,
    Column("id", String(200), primary_key=True),
    Column("tenant", String(96), nullable=False),
    Column("case_id", String(200), nullable=False),
    Column("status", String(20), nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", Text),
)
quarantine = Table(
    "quarantine",
    metadata,
    Column("id", String(200), primary_key=True),
    Column("tenant", String(96), nullable=False),
    Column("source", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("error", Text, nullable=False),
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class Conflict(Exception):
    """An idempotency key or case version conflicts with committed state."""


class NotFound(Exception):
    """No case is visible in the caller's tenant."""


class Store:
    def __init__(self, url):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")

    def initialize(self):
        metadata.create_all(self.engine)

    @contextmanager
    def connection(self):
        with self.engine.begin() as connection:
            yield connection

    def ingest(self, event_payload, result):
        tenant = event_payload["tenant_id"]
        key = digest([tenant, event_payload["event_id"]])
        fingerprint = digest(event_payload)
        response = {**result, "run_id": key, "version": 1}
        try:
            with self.connection() as connection:
                connection.execute(
                    events.insert().values(
                        id=key,
                        tenant=tenant,
                        event_id=event_payload["event_id"],
                        fingerprint=fingerprint,
                        payload=canonical(event_payload),
                        created_at=now(),
                    )
                )
                connection.execute(
                    cases.insert().values(
                        id=key,
                        tenant=tenant,
                        event_key=key,
                        status=result["status"],
                        version=1,
                        result=canonical(response),
                    )
                )
                self._audit(
                    connection,
                    key,
                    1,
                    {
                        "action": "investigation_created",
                        "at": now(),
                        "input_sha256": fingerprint,
                        "result_sha256": digest(response),
                    },
                )
                connection.execute(
                    outbox.insert().values(
                        id=key,
                        tenant=tenant,
                        case_id=key,
                        status="pending",
                        payload=canonical(event_payload["case"]),
                        attempts=0,
                    )
                )
        except IntegrityError:
            with self.engine.connect() as connection:
                existing = connection.execute(select(events).where(events.c.id == key)).mappings().one()
                if existing["fingerprint"] != fingerprint:
                    raise Conflict("Event ID was already used with different content") from None
                return self.get(tenant, key)
        return response

    def get(self, tenant, case_id):
        with self.engine.connect() as connection:
            row = connection.execute(
                select(cases.c.result).where(cases.c.id == case_id, cases.c.tenant == tenant)
            ).first()
        if row is None:
            raise NotFound("Investigation unavailable")
        return json.loads(row[0])

    def list_cases(self, tenant):
        with self.engine.connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    select(cases.c.result).where(cases.c.tenant == tenant).limit(100)
                )
            ]

    @staticmethod
    def _audit(connection, case_id, version, payload):
        previous = connection.execute(
            select(audit.c.sha256)
            .where(audit.c.case_id == case_id)
            .order_by(audit.c.version.desc())
            .limit(1)
        ).scalar()
        record = {**payload, "previous_sha256": previous, "version": version, "case_id": case_id}
        connection.execute(
            audit.insert().values(
                id=f"{case_id}:{version}",
                case_id=case_id,
                version=version,
                payload=canonical(record),
                sha256=digest(record),
            )
        )

    def review(self, tenant, case_id, actor, review, resume):
        request_key = digest([tenant, case_id, review.request_id])
        fingerprint = digest({"actor": actor, **review.model_dump()})
        try:
            with self.connection() as connection:
                old = (
                    connection.execute(select(reviews).where(reviews.c.id == request_key))
                    .mappings()
                    .first()
                )
                if old:
                    if old["fingerprint"] != fingerprint:
                        raise Conflict("Review request ID was already used with different content")
                    return json.loads(old["response"])
                row = (
                    connection.execute(
                        select(cases).where(cases.c.id == case_id, cases.c.tenant == tenant)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise NotFound("Investigation unavailable")
                if (
                    row["version"] != review.expected_version
                    or row["status"] != "awaiting_analyst_review"
                ):
                    raise Conflict("Case has changed. Reload before reviewing")
                result = json.loads(row["result"])
                decision = {
                    "action": review.action,
                    "reviewer": actor,
                    "reason": review.reason,
                    "at": now(),
                }
                resume(result, decision)
                status = {
                    "approve": "reviewed",
                    "reject": "rejected",
                    "request_evidence": "more_evidence_requested",
                }[review.action]
                result.update(status=status, version=row["version"] + 1, decision=decision)
                changed = connection.execute(
                    update(cases)
                    .where(
                        cases.c.id == case_id,
                        cases.c.version == review.expected_version,
                        cases.c.status == "awaiting_analyst_review",
                    )
                    .values(version=result["version"], status=status, result=canonical(result))
                )
                if changed.rowcount != 1:
                    raise Conflict("Another reviewer already changed this case")
                self._audit(connection, case_id, result["version"], decision)
                connection.execute(
                    reviews.insert().values(
                        id=request_key, fingerprint=fingerprint, response=canonical(result)
                    )
                )
                return result
        except IntegrityError:
            with self.engine.connect() as connection:
                old = (
                    connection.execute(select(reviews).where(reviews.c.id == request_key))
                    .mappings()
                    .first()
                )
            if old and old["fingerprint"] == fingerprint:
                return json.loads(old["response"])
            raise Conflict("Concurrent review conflict. Reload the case") from None

    def audit_trail(self, tenant, case_id):
        self.get(tenant, case_id)
        with self.engine.connect() as connection:
            return [
                {**json.loads(row.payload), "sha256": row.sha256}
                for row in connection.execute(
                    select(audit).where(audit.c.case_id == case_id).order_by(audit.c.version)
                )
            ]

    def quarantine_record(self, key, tenant, source, payload, error):
        try:
            with self.connection() as connection:
                connection.execute(
                    quarantine.insert().values(
                        id=key, tenant=tenant, source=source, payload=payload, error=error
                    )
                )
        except IntegrityError:
            pass  # Redelivery of the same Kafka partition/offset was already recorded.

    def pending_projections(self, limit=50):
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(outbox).where(outbox.c.status == "pending").limit(limit)
                ).mappings()
            ]

    def projection_result(self, key, error=None):
        with self.connection() as connection:
            connection.execute(
                update(outbox)
                .where(outbox.c.id == key)
                .values(
                    attempts=outbox.c.attempts + 1,
                    last_error=error,
                    status="pending" if error else "published",
                )
            )
