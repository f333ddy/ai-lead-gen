# eligibility_schema.py
from typing import Sequence, Mapping, Any

# Product domain, phrased as the capabilities we actually sell rather than SKU
# names, so the model classifies on function. Kept short: a long enum invites
# the model to pick something adjacent-but-wrong to avoid returning [].
PRODUCT_FIT_OPTIONS = [
    "Queue and waiting line management",
    # Distinct from the physical line above: Qtrac virtual queuing, check-in
    # and appointment kiosks, "now serving" displays, register lights. Kept as
    # its own value because it is a separate product line with its own team,
    # and because it files under IT codes that look nothing like the rest.
    "Electronic and virtual queuing",
    "Crowd control and pedestrian barriers",
    "Architectural railings and handrails",
    # Lavi's entry/exit products are PASSIVE: hinged barrier gates in post-and
    # -panel runs, and magnetic breakaway egress gates. Deliberately NOT named
    # "turnstiles" -- lavi.com confirms no turnstiles, optical turnstiles or
    # speed gates are sold, and an earlier version of this enum said otherwise
    # and caused the gate to accept turnstile procurements as leads.
    "Barrier gates and egress control",
    "Wayfinding and on-site communication",
    "In-queue merchandising",
]

# Replaces the news triggers wholesale. Every solicitation would match the old
# "Active procurement/RFP/RFQ" value, which carries no information once the
# document type is already known to be a procurement -- these describe the
# *work* instead, which is what varies.
SOLICITATION_TRIGGERS = [
    "New construction or major renovation",
    "Repair or replacement of existing facility",
    "Furniture, fixtures and equipment procurement",
    "Access control, screening or security systems",
    "Signage or wayfinding",
    "Barrier, railing or crowd control systems",
    "Requirement still being shaped (market research)",
    "Sole-source window",
]

def build_eligibility_schema(industries: Sequence[str]) -> Mapping[str, Any]:
    return {
        "name": "EligibilityDecision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "eligible": {"type": "boolean"},
                "triggers": {
                    "type": "array",
                    "items": {"enum": [
                        "New funding approved",
                        "Remodel/renovation announced",
                        "Department allocation relevant to our services",
                        "Active procurement/RFP/RFQ",
                    ]},
                    "minItems": 0,
                },
                "evidence_spans": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "quote": {"type": "string"},
                        },
                        "required": ["quote"],
                        "additionalProperties": False,
                    },
                    "minItems": 0,
                },
                "extracted": {
                    "type": "object",
                    "properties": {
                        "amount_usd": {"type": ["number", "null"]},
                        "fiscal_year": {"type": ["integer", "null"]},
                        "location": {"type": ["string", "null"]},
                        "doc_date": {"type": ["string", "null"]},
                        "summary": {"type": ["string", "null"]},
                        "article_link": {"type": ["string", "null"]},
                        "title": {"type": ["string", "null"]},
                        "industries": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(industries),
                            },
                            "minItems": 0,  # default; tightened via conditional when eligible=true
                        },
                        "company": {"type": ["string", "null"]}
                    },
                    "required": [
                        "amount_usd",
                        "fiscal_year",
                        "location",
                        "doc_date",
                        "summary",
                        "article_link",
                        "title",
                        "industries",
                        "company"
                    ],
                    "additionalProperties": False,
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "eligible",
                "triggers",
                "evidence_spans",
                "confidence",
                "extracted",
            ],
            "additionalProperties": False
        },
    }


def build_solicitation_schema(industries: Sequence[str]) -> Mapping[str, Any]:
    """Eligibility schema for SAM.gov contract opportunities.

    A superset of the news schema, not a replacement: every key
    db.insert_enriched_document reads is preserved with the same name and type,
    so solicitations persist through the existing write path with no migration.
    The three added keys (product_fit, facility_type, recommended_action) are
    ignored by _raw_document_row and insert_enriched_document, and are there for
    the digest email and for filtering.

    Note `strict: True` requires every property to appear in `required` --
    optionality is expressed with a null type, never by omission.
    """
    return {
        "name": "SolicitationEligibilityDecision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "eligible": {"type": "boolean"},
                "triggers": {
                    "type": "array",
                    "items": {"enum": list(SOLICITATION_TRIGGERS)},
                    "minItems": 0,
                },
                "evidence_spans": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "quote": {"type": "string"},
                        },
                        "required": ["quote"],
                        "additionalProperties": False,
                    },
                    "minItems": 0,
                },
                "extracted": {
                    "type": "object",
                    "properties": {
                        # Present for DB compatibility. Award$ is 0% populated on
                        # solicitations, so amount_usd is null on nearly every row
                        # and its absence must not cost confidence.
                        "amount_usd": {"type": ["number", "null"]},
                        "fiscal_year": {"type": ["integer", "null"]},
                        "location": {"type": ["string", "null"]},
                        "doc_date": {"type": ["string", "null"]},
                        "summary": {"type": ["string", "null"]},
                        "article_link": {"type": ["string", "null"]},
                        "title": {"type": ["string", "null"]},
                        "industries": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(industries),
                            },
                            "minItems": 0,
                        },
                        "company": {"type": ["string", "null"]},
                        # Added for solicitations.
                        "product_fit": {
                            "type": "array",
                            "items": {"enum": list(PRODUCT_FIT_OPTIONS)},
                            "minItems": 0,
                        },
                        "facility_type": {"type": ["string", "null"]},
                        "recommended_action": {"type": ["string", "null"]},
                    },
                    "required": [
                        "amount_usd",
                        "fiscal_year",
                        "location",
                        "doc_date",
                        "summary",
                        "article_link",
                        "title",
                        "industries",
                        "company",
                        "product_fit",
                        "facility_type",
                        "recommended_action",
                    ],
                    "additionalProperties": False,
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "eligible",
                "triggers",
                "evidence_spans",
                "confidence",
                "extracted",
            ],
            "additionalProperties": False,
        },
    }
