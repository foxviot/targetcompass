from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .ids import make_stable_id
from .schemas import AnalysisTaskPacket, ReviewTaskPacket


def build_analysis_task_packet(
    *,
    subquestion_id: str,
    method_name: str,
    expected_inputs: list[str],
    expected_outputs: list[str],
    qc_requirements: list[str],
    failure_conditions: list[str],
) -> dict[str, Any]:
    task_id = make_stable_id(
        "analysis_task",
        {
            "subquestion_id": subquestion_id,
            "method_name": method_name,
            "expected_inputs": expected_inputs,
            "expected_outputs": expected_outputs,
        },
    )
    packet = AnalysisTaskPacket(
        task_id=task_id,
        subquestion_id=subquestion_id,
        expected_inputs=expected_inputs,
        expected_outputs=expected_outputs,
        qc_requirements=qc_requirements,
        failure_conditions=failure_conditions,
    )
    data = asdict(packet)
    data["packet_type"] = "AnalysisTaskPacket"
    data["method_name"] = method_name
    data["code_change_instructions"] = []
    return data


def build_review_task_packet(
    *,
    subquestion_id: str,
    audit_scope: list[str],
    claim_ceiling: str,
    required_checks: list[str],
) -> dict[str, Any]:
    task_id = make_stable_id(
        "review_task",
        {
            "subquestion_id": subquestion_id,
            "audit_scope": audit_scope,
            "claim_ceiling": claim_ceiling,
        },
    )
    packet = ReviewTaskPacket(
        task_id=task_id,
        audit_scope=audit_scope,
        claim_ceiling=claim_ceiling,
        required_checks=required_checks,
    )
    data = asdict(packet)
    data["packet_type"] = "ReviewTaskPacket"
    data["subquestion_id"] = subquestion_id
    return data


def validate_task_packets(task_packets: list[dict[str, Any]]) -> list[str]:
    errors = []
    for packet in task_packets:
        packet_type = packet.get("packet_type")
        if packet_type == "AnalysisTaskPacket":
            if packet.get("code_change_instructions"):
                errors.append(f"{packet.get('task_id')}: AnalysisTaskPacket cannot contain code change instructions")
            for field in ["subquestion_id", "expected_inputs", "expected_outputs", "qc_requirements", "failure_conditions"]:
                if not packet.get(field):
                    errors.append(f"{packet.get('task_id')}: {field} is required")
        elif packet_type == "EngineeringTaskPacket":
            for field in ["allowed_paths", "forbidden_paths", "expected_patch_summary", "test_commands"]:
                if not packet.get(field):
                    errors.append(f"{packet.get('task_id')}: {field} is required")
        elif packet_type == "ReviewTaskPacket":
            for field in ["audit_scope", "claim_ceiling", "required_checks"]:
                if not packet.get(field):
                    errors.append(f"{packet.get('task_id')}: {field} is required")
        else:
            errors.append(f"{packet.get('task_id')}: unknown packet_type {packet_type}")
    return errors
