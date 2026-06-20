from .contracts import MethodContext, MethodResult, MethodSpec
from ..experiment_design import design_experiments


def local_experiment_design(context: MethodContext) -> MethodResult:
    designs = design_experiments(context.project_dir)
    return MethodResult(
        status="pass" if designs else "review",
        message=f"{len(designs)} experiment design draft(s) prepared by the local method.",
        details={"design_count": len(designs)},
    )


def review_first_experiment_design(context: MethodContext) -> MethodResult:
    designs = design_experiments(context.project_dir, max_designs=3)
    for design in designs:
        design.setdefault("risks", []).append("Review-first method: require human or skill approval before wet-lab execution.")
    return MethodResult(
        status="review",
        message=f"{len(designs)} review-first experiment design draft(s) prepared.",
        details={"design_count": len(designs), "requires_review": True},
    )


METHODS = [
    MethodSpec(
        method_id="local_experiment_design_v0",
        stage="experiment",
        label="Local experiment design v0",
        description="Default experiment design drafting from feasible ideas.",
        runner=local_experiment_design,
    ),
    MethodSpec(
        method_id="review_first_experiment_design_v0",
        stage="experiment",
        label="Review-first experiment design v0",
        description="Conservative experiment design mode requiring human or skill review before wet-lab execution.",
        runner=review_first_experiment_design,
    ),
]
