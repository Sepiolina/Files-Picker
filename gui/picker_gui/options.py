"""
All choices shown in the UI, with the engine value they map to.

Engine values (left) are what goes into the job JSON - see docs/PROTOCOL.md.
Labels (right) are what the user reads. Keep this file the single place to
rename or reorder an option.
"""

import copy

SEPARATORS = [
    ("newline", "New line"),
    ("comma", "Comma  ,"),
    ("semicolon", "Semicolon  ;"),
    ("tab", "Tab"),
    ("pipe", "Pipe  |"),
    ("whitespace", "Any space"),
    ("custom", "Custom…"),
]
TABULAR_SEPARATORS = {"comma", "semicolon", "tab", "pipe", "custom"}

MATCH_MODES = [
    ("whole", "Whole part", "abc  →  gg_abc_1.txt ✓   gg_abcde_1.txt ✗"),
    ("contains", "Anywhere", "abc  →  gg_abc_1.txt ✓   gg_abcde_1.txt ✓"),
    ("exact", "Exact name", "abc  →  abc.txt ✓   gg_abc_1.txt ✗"),
    ("prefix", "Starts with", "abc  →  abc_1.txt ✓   gg_abc.txt ✗"),
    ("suffix", "Ends with", "abc  →  gg_abc.txt ✓   abc_1.txt ✗"),
]
TARGETS = [
    ("stem", "File name (no extension)"),
    ("name", "File name + extension"),
    ("path", "Path inside the folder"),
]
DUPLICATES = [
    ("first", "Merge, keep first spelling"),
    ("last", "Merge, keep last spelling"),
    ("error", "Stop and show me"),
]
OVERLAP = [
    ("longest", "Longest name wins"),
    ("shortest", "Shortest name wins"),
    ("first", "First in list wins"),
    ("all", "Use every match"),
]
OUTPUT_TYPES = [
    ("zip", "Zip"),
    ("folder", "Folder"),
    ("tar", "Tar"),
    ("targz", "Tar.gz"),
]
ARCHIVE_EXT = {"zip": ".zip", "tar": ".tar", "targz": ".tar.gz"}
COMPRESSION = [
    ("store", "Fastest (no compression)"),
    ("deflate", "Smaller (compress)"),
]
CONFLICT = [
    ("rename", "Keep both (rename new)"),
    ("overwrite", "Overwrite"),
    ("skip", "Skip"),
]

# Mirrors job.Default() in the engine.
DEFAULT_JOB = {
    "source": "",
    "recursive": True,
    "list": {"text": "", "file": "", "separator": "newline", "custom": "",
             "csvQuotes": True, "header": False, "column": "", "trim": True},
    "match": {"mode": "whole", "target": "stem", "caseSensitive": False,
              "duplicates": "first", "overlap": "longest", "extensions": []},
    "output": {"type": "zip", "path": "", "groupByName": False, "report": True,
               "compression": "store", "conflict": "rename"},
    "previewLimit": 5000,
}


def default_job():
    return copy.deepcopy(DEFAULT_JOB)


def merged_job(saved):
    """Lay a saved (possibly older / partial) job over the defaults."""
    job = default_job()
    for key, value in (saved or {}).items():
        if isinstance(value, dict) and isinstance(job.get(key), dict):
            job[key].update(value)
        elif key in job:
            job[key] = value
    return job


def label(options, value):
    for opt in options:
        if opt[0] == value:
            return opt[1]
    return value


def matching_summary(m):
    """One line that describes the matching rules, shown when they are collapsed."""
    parts = [
        label(MATCH_MODES, m["mode"]),
        label(TARGETS, m["target"]).lower(),
        "case-sensitive" if m["caseSensitive"] else "any case",
        label(OVERLAP, m["overlap"]).lower(),
    ]
    if m.get("extensions"):
        parts.append("only " + ", ".join(m["extensions"]))
    return " · ".join(parts)


# Short labels for the summary chips shown when a section is collapsed.
_SHORT = {
    "stem": "No extension", "name": "With extension", "path": "Full path",
    "longest": "Longest wins", "shortest": "Shortest wins", "first": "First in list wins", "all": "All matches",
    "store": "No compression", "deflate": "Compressed",
    "rename": "Keep both", "overwrite": "Overwrite", "skip": "Skip existing",
}

EXPORT_VERB = {"zip": "Export zip", "folder": "Copy to folder", "tar": "Export tar", "targz": "Export tar.gz"}


def matching_tokens(m):
    tokens = [label(MATCH_MODES, m["mode"]), _SHORT[m["target"]],
              "Case-sensitive" if m["caseSensitive"] else "Any case", _SHORT[m["overlap"]]]
    if m.get("extensions"):
        tokens.append(", ".join(e.lstrip(".") for e in m["extensions"]))
    return tokens


def output_tokens(o):
    tokens = [_SHORT[o["compression"]] if o["type"] == "zip" else None,
              _SHORT[o["conflict"]] if o["type"] == "folder" else None,
              "Folder per name" if o["groupByName"] else "Original structure",
              "CSV report" if o["report"] else "No report"]
    return [t for t in tokens if t]
