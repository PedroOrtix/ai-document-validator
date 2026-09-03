"""Engine-lane definitions and decision-table helpers for the golden-set eval."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any

from docvalidator.domain.models import DocumentExtraction
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.offline import OfflineExtractor

# Published z-ai/glm-5.3-flash OpenRouter prices, USD per token.
GLM_FLASH_PROMPT_PRICE_PER_TOKEN = 0.000000075
GLM_FLASH_COMPLETION_PRICE_PER_TOKEN = 0.00000025
GLM_FLASH_PRICE_PER_TOKEN = GLM_FLASH_PROMPT_PRICE_PER_TOKEN + GLM_FLASH_COMPLETION_PRICE_PER_TOKEN

LANE_NAMES = ("offline", "slm", "vlm", "ocr", "auto")
LANE_FORMATS: dict[str, tuple[str, ...]] = {
    "offline": ("txt", "pdf", "scanned"),
    "slm": ("txt", "pdf"),
    "vlm": ("scanned", "pdf"),
    "ocr": ("scanned", "pdf"),
    "auto": ("txt", "pdf", "scanned"),
}


@dataclass(frozen=True)
class LanePlan:
    """One runnable extraction lane and its eligible golden-set formats."""

    name: str
    formats: tuple[str, ...]
    available: bool
    skip_reason: str | None = None


def _has_ocr_dependency() -> bool:
    return find_spec("rapidocr_onnxruntime") is not None


def resolve_lane_plans(
    requested: tuple[str, ...],
    *,
    live: bool,
    has_api_key: bool,
) -> list[LanePlan]:
    """Resolve CLI lane requests to eligible, runnable lanes."""
    requested = LANE_NAMES if "all" in requested else requested
    invalid = [lane for lane in requested if lane not in LANE_NAMES]
    if invalid:
        raise ValueError(f"unknown lanes: {', '.join(invalid)}")

    plans: list[LanePlan] = []
    for name in LANE_NAMES:
        if name not in requested:
            continue
        if name in {"slm", "vlm", "auto"}:
            if not live:
                plans.append(LanePlan(name, LANE_FORMATS[name], False, "requires --live"))
            elif not has_api_key:
                plans.append(
                    LanePlan(
                        name,
                        LANE_FORMATS[name],
                        False,
                        "requires OPENROUTER_API_KEY",
                    )
                )
            else:
                plans.append(LanePlan(name, LANE_FORMATS[name], True))
        else:
            plans.append(LanePlan(name, LANE_FORMATS[name], True))
    return plans


def default_lane_request(*, has_ocr_extra: bool | None = None) -> tuple[str, ...]:
    """Return the network-free lanes available in this environment."""
    ocr_available = _has_ocr_dependency() if has_ocr_extra is None else has_ocr_extra
    lanes = ["offline"]
    if ocr_available:
        lanes.append("ocr")
    return tuple(lanes)


def estimate_cost_usd(total_tokens: float, *, lane: str) -> float:
    """Estimate OpenRouter cost for LLM lanes; local/deterministic lanes are free.

    The extractor records provider total tokens, so apply the blended
    prompt-plus-completion price. This is conservative for completion-heavy
    documents and avoids assuming an undocumented prompt/completion split.
    """
    if lane not in {"slm", "vlm", "auto"}:
        return 0.0
    return total_tokens * GLM_FLASH_PRICE_PER_TOKEN


def extraction_telemetry(extraction: DocumentExtraction) -> dict[str, float | int | None]:
    """Read per-document timing/token telemetry from extraction metadata."""
    return {
        "duration_ms": extraction.metadata.duration_ms,
        "total_tokens": extraction.metadata.total_tokens,
    }


def decision_table(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one compact row per lane, format, and tier from prepared report lanes."""
    rows: list[dict[str, Any]] = []
    for engine_lane in report["lanes"].values():
        for format_name in engine_lane["formats"]:
            for tier in (0, 1, 2):
                metrics = engine_lane["slices"].get(f"tier:{tier}")
                if metrics is None:
                    continue
                tokens = [
                    result["total_tokens"]
                    for result in engine_lane["results"]
                    if result["slices"].get("format") == format_name
                    and result["slices"].get("tier") == tier
                    and result["total_tokens"] is not None
                ]
                durations = [
                    result["duration_ms"]
                    for result in engine_lane["results"]
                    if result["slices"].get("format") == format_name
                    and result["slices"].get("tier") == tier
                    and result["duration_ms"] is not None
                ]
                rows.append(
                    {
                        "lane": engine_lane["lane"],
                        "format": format_name,
                        "tier": tier,
                        "field_accuracy": metrics["field_accuracy"],
                        "verdict_agreement": metrics["verdict_agreement"],
                        "avg_ms": (sum(durations) / len(durations)) if durations else None,
                        "avg_tokens": (sum(tokens) / len(tokens)) if tokens else None,
                        "est_cost_per_doc": (
                            (sum(tokens) / len(tokens)) * GLM_FLASH_PRICE_PER_TOKEN
                            if tokens
                            else 0.0
                        ),
                    }
                )
                if engine_lane["lane"] == "auto":
                    for sub_route in ("llm", "vlm", "ocr"):
                        route_results = [
                            result
                            for result in engine_lane["results"]
                            if result["slices"].get("format") == format_name
                            and result["slices"].get("tier") == tier
                            and result.get("sub_route") == sub_route
                        ]
                        tokens = [
                            result["total_tokens"]
                            for result in route_results
                            if result["total_tokens"] is not None
                        ]
                        durations = [
                            result["duration_ms"]
                            for result in route_results
                            if result["duration_ms"] is not None
                        ]
                        rows.append(
                            {
                                "lane": f"auto:{sub_route}",
                                "format": format_name,
                                "tier": tier,
                                "field_accuracy": metrics["field_accuracy"],
                                "verdict_agreement": metrics["verdict_agreement"],
                                "avg_ms": (sum(durations) / len(durations)) if durations else None,
                                "avg_tokens": (sum(tokens) / len(tokens)) if tokens else None,
                                "est_cost_per_doc": (
                                    (sum(tokens) / len(tokens)) * GLM_FLASH_PRICE_PER_TOKEN
                                    if tokens
                                    else 0.0
                                ),
                            }
                        )
    return rows


def print_decision_table(report: dict[str, Any]) -> None:
    """Print the decision table and one aggregate line per engine lane."""
    rows = decision_table(report)
    print("\nDECISION TABLE")
    print(
        f"{'lane':<8} {'format':<8} {'tier':>4} {'fields':>8} {'verdict':>8} "
        f"{'avg_ms':>9} {'tokens':>9} {'cost/doc':>11}"
    )
    for row in rows:
        duration = "-" if row["avg_ms"] is None else f"{row['avg_ms']:.1f}"
        tokens = "-" if row["avg_tokens"] is None else f"{row['avg_tokens']:.0f}"
        cost = f"${row['est_cost_per_doc']:.8f}"
        print(
            f"{row['lane']:<8} {row['format']:<8} {row['tier']:>4} "
            f"{row['field_accuracy']:>8.2%} {row['verdict_agreement']:>8.2%} "
            f"{duration:>9} {tokens:>9} {cost:>11}"
        )

    print("\nLANE SUMMARY")
    for engine_lane in report["lanes"].values():
        print(
            f"{engine_lane['lane']:<8} formats={','.join(engine_lane['formats']):<15} "
            f"field_accuracy={engine_lane['aggregate']['field_accuracy']:.4f} "
            f"verdict_agreement={engine_lane['aggregate']['verdict_agreement']:.4f}"
        )


def make_offline_extractor() -> Extractor:
    """Production factory for the network-free offline lane."""
    return OfflineExtractor()


def make_auto_extractor() -> Extractor:
    """Production factory for the multi-route auto lane."""
    from docvalidator.extraction.routing import AutoExtractor

    return AutoExtractor()
