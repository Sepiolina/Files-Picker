# How the GUI talks to the engine

The GUI and the engine are separate programs. The GUI never touches your files; it only starts the engine and shows what the engine reports. Anything that can start a process and read lines can use the engine, including scripts, a scheduled task, or another GUI.

```
GUI ──(1 line: job JSON)──▶ picker export --stdin-control ──(JSON lines)──▶ GUI
GUI ──("cancel")──────────▶                                    (optional)
```

## Running the engine

```
picker <command> [--job job.json] [--format json|text] [--stdin-control]
```

| Command   | What it does                                              |
|-----------|-----------------------------------------------------------|
| `inspect` | parse the name list only (entries / unique / duplicates)  |
| `scan`    | find matching files; nothing is written                   |
| `export`  | scan, then write the zip / tar / folder and the reports   |
| `tree`    | list a whole folder tree: names, sizes, times (see below) |
| `hash`    | checksum a list of files in parallel (see below)          |
| `example` | print a full job file with every default                  |
| `version` | print the version                                         |

- If you don't pass `--job`, the job is the **first line of stdin**.
- With `--stdin-control`, a later stdin line `cancel` (or stdin closing) stops the run. Partially written archives and files are removed.
- `--format text` prints readable progress instead of JSON, which is handy in a terminal.

## Job (input)

```jsonc
{
  "source": "D:/data",                 // folder to search
  "recursive": true,                   // include sub-folders
  "list": {
    "text": "ABC\nHWUDS",              // names... (or leave empty and use "file")
    "file": "",                        // path to a .txt / .csv
    "separator": "newline",            // newline|comma|semicolon|tab|pipe|whitespace|custom
    "custom": "",                      // used when separator = custom
    "csvQuotes": true, "header": false,
    "column": "",                      // "2" or "PartNo"; empty = all columns
    "trim": true
  },
  "match": {
    "mode": "whole",                   // whole|contains|exact|prefix|suffix
    "target": "stem",                  // stem (no extension)|name|path
    "caseSensitive": false,
    "duplicates": "first",             // first|last|error
    "overlap": "longest",              // longest|shortest|first|all
    "extensions": []                   // e.g. ["pdf","jpg"]; empty = all
  },
  "output": {
    "type": "zip",                     // zip|folder|tar|targz
    "path": "D:/out/matched.zip",
    "groupByName": false,              // ABC/<original path>, HWUDS/<original path>
    "report": true,                    // <name>_files.csv + <name>_names.csv
    "compression": "store",            // zip: store|deflate
    "conflict": "rename"               // folder: rename|overwrite|skip
  },
  "previewLimit": 5000                 // max file rows in the "preview" event
}
```

Any field you leave out keeps its default. Run `picker example` to see them all.

## Events (output)

The engine prints one JSON object per line. Every object has a `type`.

| type        | when                          | main fields |
|-------------|-------------------------------|-------------|
| `list`      | the list was parsed           | `entries`, `unique`, `duplicated`, `duplicateGroups` |
| `progress`  | about 7× per second           | `phase` (`scan` / `export`), `checked`, `matched`, `bytes`, `totalBytes`, `files`, `totalFiles` |
| `preview`   | the scan finished             | `checked`, `matched`, `totalBytes`, `notFound`, `truncated`, `files[]`, `names[]`, `warnings[]` |
| `done`      | success (always the last line) | `command`, `output`, `reports[]`, `exported`, `bytes`, `warnings[]`, `elapsedMs` |
| `error`     | failure (always the last line) | `message` |
| `cancelled` | the run was cancelled (last line) | – |

Exit codes: `0` ok · `1` error · `2` bad arguments · `130` cancelled.

# The `tree` and `hash` commands (version 2.1)

These two commands serve the GUI's Compare, Snapshots, Duplicates, Space and Find tabs. They never read file contents except `hash`, and they use their own small jobs. The same `--job`, `--format` and `--stdin-control` options apply.

## `tree` - list a folder tree

Job:

```json
{ "root": "/data", "skipHidden": true, "skipCloud": true,
  "ignore": "node_modules/\n*.tmp", "known": "/tmp/known.jsonl", "workers": 0 }
```

| field | meaning |
|-------|---------|
| `root` | folder to list (required) |
| `skipHidden` | skip hidden files (dot-files, the Windows hidden attribute) and well-known system folders |
| `skipCloud` | skip online-only cloud placeholders (Windows `RECALL_ON_*` / `OFFLINE` attributes, macOS `SF_DATALESS`, `.name.icloud`) |
| `ignore` | ignore rules, one per line (`.gitignore` subset: `name/`, `*.tmp`, `/top`, `a/b`, `**`, `# comment`; no `!`) |
| `known` | optional path to JSON lines `{"rel":"a/b","m":<mtime ns>,"d":["subdir",...]}` from an earlier scan. A directory whose own mtime still matches is **not listed again** and is reported with `"same": true` |
| `workers` | concurrent directory reads (0 = automatic) |

Events - one per directory, in no particular order, then progress and a final `done`:

```json
{"type":"dir","rel":"docs/deep","m":1795394054244072653,"f":[["c.bin",12,1795394054244072653]],"d":["sub"]}
{"type":"dir","rel":"photos","m":1795394054244072653,"same":true,"f":[],"d":["2024"]}
{"type":"progress","phase":"tree","files":1200,"dirs":85,"bytes":93422112}
{"type":"done","command":"tree","files":1200,"dirs":85,"bytes":93422112,"errors":0,"reused":40,"cloud":3,"ignored":17,"elapsedMs":212}
```

`rel` is `""` for the root and always uses `/`. Files are `[name, size, mtimeNs]`. For `"same": true` the caller keeps the file list it saved earlier. Symlinks are never followed.

## `hash` - checksum files

Job: `{ "root": "/data", "files": ["a/x.txt", "b/y.bin"], "partial": 0, "workers": 4 }`

`partial > 0` hashes only the first that many bytes (used to rule out most non-duplicates cheaply). The checksum is **SHA-256 shortened to its first 16 bytes (32 hex characters)**.

```json
{"type":"hashes","h":{"a/x.txt":"98ea6e4f216f2fb4b69fff9b3a44842c","b/y.bin":""}}
{"type":"progress","phase":"hash","files":2,"bytes":1048576,"totalFiles":2}
{"type":"done","command":"hash","files":2,"bytes":1048576,"elapsedMs":12}
```

An empty string means the file could not be read. Cancelling ends the run with a `cancelled` event, as for the other commands.
