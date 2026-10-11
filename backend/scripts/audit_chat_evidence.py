"""Audit saved Chat smoke evidence without loading .env or contacting providers."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.domain.models import ChatReport, Report, Source
from app.research.audit import audit_evidence


def audit_artifact(artifact):
    cases = []
    for case in artifact.get("cases", []):
        try:
            data = case.get("report")
            model = (
                ChatReport
                if data and any("passage_id" in c for c in data.get("claims", []))
                else Report
            )
            report = model.model_validate(data) if data else None
            # Use the captured extraction corpus, never substitute a model-provided URL list.
            sources = [Source.model_validate(s) for s in case.get("retrieved_sources", [])]
            audit = audit_evidence(report, sources, case.get("evidence_passages"))
        except ValidationError:
            audit = {
                "status": "invalid_report_or_source_schema",
                "structural_checks_passed": False,
                "claims": [],
            }
        cases.append({"case": case.get("case"), "job_status": case.get("status"), "audit": audit})
    return {"audit_version": 2, "network_calls": 0, "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Saved local live-chat JSON artifact")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = args.output or args.input.with_name(args.input.stem + "-evidence-audit.json")
    if target.resolve() == args.input.resolve():
        parser.error("Audit output must not overwrite the original artifact")
    result = audit_artifact(json.loads(args.input.read_text(encoding="utf-8")))
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "artifact": str(target.resolve()),
                "network_calls": 0,
                "cases": [
                    {"case": c["case"], "status": c["audit"]["status"]} for c in result["cases"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
