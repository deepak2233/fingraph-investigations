"""Application boundary shared by HTTP and Kafka ingestion."""

from api.core import investigate, resume_review
from pipeline.contracts import CaseEvent


class InvalidEvidence(ValueError):
    """A case passed the wire contract but failed semantic validation."""


class InvestigationService:
    def __init__(self, store):
        self.store = store

    def ingest(self, event):
        event = CaseEvent.model_validate(event)
        payload = event.model_dump(mode="json")
        result = investigate(payload["case"])
        if result["status"] == "quarantined":
            raise InvalidEvidence("RDF validation failed")
        result["provenance"]["review_storage"] = (
            "SQL snapshot with versioned review and transactional audit"
        )
        return self.store.ingest(payload, result)

    def review(self, tenant, case_id, actor, body):
        return self.store.review(tenant, case_id, actor, body, resume_review)
