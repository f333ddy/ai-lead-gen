# eligibility_schema.py
from typing import Sequence, Mapping, Any

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
