import json
from pathlib import Path

from .ideas import load_ideas


def design_experiments(project_dir: Path, max_designs: int = 5) -> list[dict]:
    ideas = load_ideas(project_dir)
    candidates = [idea for idea in ideas if idea.get("execution_status") == "candidate"]
    if not candidates:
        candidates = ideas[:max_designs]
    designs = []
    for idea in candidates[:max_designs]:
        designs.append(
            {
                "idea_id": idea["idea_id"],
                "title": idea["title"],
                "objective": f"Validate feasibility of {idea['title']} for {idea.get('research_prompt', '').strip()}",
                "in_silico_steps": [
                    "Confirm differential expression across selected datasets.",
                    "Check accessibility and safety annotation gates.",
                    "Review enrichment context and evidence traceability.",
                ],
                "wet_lab_followup": [
                    "qPCR or targeted proteomics in independent case/control samples.",
                    "Perturbation assay in relevant endothelial or immune-cell model.",
                    "Specificity check against critical normal tissues.",
                ],
                "acceptance_criteria": [
                    "Replicated direction across at least two evidence sources when available.",
                    "No hard safety gate failure.",
                    "Clear assay path for target modulation or detection.",
                ],
                "risks": idea.get("blockers", []) or ["Requires expert review before experimental spend."],
            }
        )
    out_dir = project_dir / "results" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "experiment_designs.json"
    md_path = out_dir / "experiment_designs.md"
    json_path.write_text(json.dumps(designs, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Experiment Design Drafts", ""]
    for design in designs:
        lines.extend(
            [
                f"## {design['title']}",
                f"- idea_id: {design['idea_id']}",
                f"- objective: {design['objective']}",
                "- in_silico_steps:",
            ]
        )
        lines.extend(f"  - {step}" for step in design["in_silico_steps"])
        lines.append("- wet_lab_followup:")
        lines.extend(f"  - {step}" for step in design["wet_lab_followup"])
        lines.append("- acceptance_criteria:")
        lines.extend(f"  - {step}" for step in design["acceptance_criteria"])
        lines.append("- risks:")
        lines.extend(f"  - {risk}" for risk in design["risks"])
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [json_path, md_path],
            producer="experiment_design",
            artifact_type="experiment_design_output",
            task_id="experiment_design",
            qc_status="review",
        )
    except Exception:
        pass
    return designs
