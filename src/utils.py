"""Utility helpers for notebook-side MongoDB analysis."""

import pandas as pd


def check_field(doc, field_path):
    """Check whether a nested MongoDB field path exists."""
    keys = field_path.split(".")
    value = doc

    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return False, None
        value = value[key]

    return True, value


# Identify consecutive empty months (length ≥ 2) within each listing-year
def detect_empty_sequences(group):
    "Identify consecutive sequences of empty months (no reviews) within a listing-year."
    months = group["month"].tolist()
    active = group["has_review"].tolist()

    sequences = []
    current_seq = []

    for m, a in zip(months, active):
        if not a:
            current_seq.append(m)
        else:
            if len(current_seq) >= 2:
                sequences.append(current_seq)
            current_seq = []

    # Catch sequence at end of year
    if len(current_seq) >= 2:
        sequences.append(current_seq)

    return sequences


# ----------------------------------
# Explain table maker
# ----------------------------------
_DEFAULT_VERSION_LABELS = ("before_optimization", "after_optimization")
_COMPARISON_COLUMNS = [
    "version",
    "stage_type",
    "execution_time_ms",
    "documents_examined",
    "keys_examined",
    "memory_usage",
]


def _get_cursor_stage(explain_doc):
    """Return the aggregation cursor stage."""
    return explain_doc["stages"][0]["$cursor"]


def _extract_stage_type(explain_doc):
    """Return the terminal scan stage from the winning plan."""
    current_stage = _get_cursor_stage(explain_doc)["queryPlanner"]["winningPlan"]
    while "inputStage" in current_stage:
        current_stage = current_stage["inputStage"]
    return current_stage["stage"]


def _extract_execution_metrics(explain_doc):
    """Return execution metrics from the cursor execution stats."""
    execution_stats = _get_cursor_stage(explain_doc)["executionStats"]
    return {
        "execution_time_ms": execution_stats["executionTimeMillis"],
        "documents_examined": execution_stats["totalDocsExamined"],
        "keys_examined": execution_stats["totalKeysExamined"],
    }


def _extract_memory_usage(explain_doc):
    """Return memory usage from the aggregation stages."""
    stages = explain_doc["stages"]

    total_memory_usage = [
        stage["maxTotalMemoryUsageBytes"]
        for stage in stages
        if "maxTotalMemoryUsageBytes" in stage
    ]
    if total_memory_usage:
        return max(total_memory_usage)

    function_memory_usage = [
        max(stage["maxFunctionMemoryUsageBytes"].values())
        for stage in stages
        if "maxFunctionMemoryUsageBytes" in stage
    ]
    if function_memory_usage:
        return max(function_memory_usage)

    used_disk = [stage["usedDisk"] for stage in stages if "usedDisk" in stage]
    if used_disk:
        return any(used_disk)

    return None


def _build_explain_row(explain_doc, version):
    """Build a comparison row for one explain document."""
    execution_metrics = _extract_execution_metrics(explain_doc)
    return {
        "version": version,
        "stage_type": _extract_stage_type(explain_doc),
        "execution_time_ms": execution_metrics["execution_time_ms"],
        "documents_examined": execution_metrics["documents_examined"],
        "keys_examined": execution_metrics["keys_examined"],
        "memory_usage": _extract_memory_usage(explain_doc),
    }


def _resolve_version_labels(explain_docs, labels):
    """Return validated version labels for explain comparison rows."""
    if len(explain_docs) < 2:
        raise ValueError(
            "compare_explain_outputs requires at least two explain documents."
        )

    if labels is None:
        if len(explain_docs) == 2:
            return list(_DEFAULT_VERSION_LABELS)
        raise ValueError(
            "compare_explain_outputs requires labels when comparing more than "
            "two explain documents."
        )

    if isinstance(labels, str):
        raise ValueError("labels must be an iterable of non-empty strings.")

    version_labels = list(labels)
    if len(version_labels) != len(explain_docs):
        raise ValueError(
            "labels must contain exactly one version name per explain document."
        )

    if any(not isinstance(label, str) or not label for label in version_labels):
        raise ValueError("labels must be non-empty strings.")

    return version_labels


def compare_explain_outputs(*explain_docs, labels=None) -> pd.DataFrame:
    """Return a DataFrame comparing two or more aggregation explain outputs."""
    version_labels = _resolve_version_labels(explain_docs, labels)
    comparison_rows = [
        _build_explain_row(explain_doc, version)
        for version, explain_doc in zip(version_labels, explain_docs)
    ]
    return pd.DataFrame(comparison_rows, columns=_COMPARISON_COLUMNS)
