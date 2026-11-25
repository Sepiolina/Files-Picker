// Package events is the engine's output protocol: one JSON object per line
// on stdout ("NDJSON"). Every object has a "type" field. See docs/PROTOCOL.md.
//
// With --format text the same events are printed for humans instead.
package events

import (
	"encoding/json"
	"fmt"
	"io"
	"sync"
)

// List: result of parsing the name list.
type List struct {
	Type       string     `json:"type"` // "list"
	Entries    int        `json:"entries"`
	Unique     int        `json:"unique"`
	Duplicated int        `json:"duplicated"`
	Groups     [][]string `json:"duplicateGroups"` // first 200 groups of duplicate spellings
}

// Progress: sent ~7 times a second while scanning or exporting.
type Progress struct {
	Type       string `json:"type"`  // "progress"
	Phase      string `json:"phase"` // "scan" | "export"
	Checked    int64  `json:"checked,omitempty"`
	Matched    int64  `json:"matched,omitempty"`
	Bytes      int64  `json:"bytes,omitempty"`
	TotalBytes int64  `json:"totalBytes,omitempty"`
	Files      int64  `json:"files,omitempty"`
	TotalFiles int64  `json:"totalFiles,omitempty"`
}

// Preview: scan results (file rows capped at previewLimit; all names included).
type Preview struct {
	Type       string        `json:"type"` // "preview"
	Checked    int64         `json:"checked"`
	Matched    int           `json:"matched"`
	TotalBytes int64         `json:"totalBytes"`
	NotFound   int           `json:"notFound"`
	Truncated  bool          `json:"truncated"`
	Files      []PreviewFile `json:"files"`
	Names      []NameCount   `json:"names"`
	Warnings   []string      `json:"warnings"`
}

type PreviewFile struct {
	Path     string   `json:"path"`
	Size     int64    `json:"size"`
	Matched  []string `json:"matched"`
	Assigned []string `json:"assigned"`
}

type NameCount struct {
	Name      string   `json:"name"`
	Count     int      `json:"count"`
	Spellings []string `json:"spellings,omitempty"` // only when duplicated
}

// Done: the command finished successfully.
type Done struct {
	Type      string   `json:"type"` // "done"
	Command   string   `json:"command"`
	Output    string   `json:"output,omitempty"`
	Reports   []string `json:"reports,omitempty"`
	Exported  int      `json:"exported"`
	Bytes     int64    `json:"bytes"`
	Warnings  []string `json:"warnings"`
	ElapsedMs int64    `json:"elapsedMs"`
}

type Error struct {
	Type    string `json:"type"` // "error"
	Message string `json:"message"`
}

type Cancelled struct {
	Type string `json:"type"` // "cancelled"
}

// Emitter writes events; safe for use from several goroutines.
type Emitter struct {
	mu   sync.Mutex
	w    io.Writer
	text bool
}

func NewEmitter(w io.Writer, text bool) *Emitter { return &Emitter{w: w, text: text} }

func (e *Emitter) Emit(ev any) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.text {
		fmt.Fprint(e.w, human(ev))
		return
	}
	b, _ := json.Marshal(ev)
	e.w.Write(append(b, '\n'))
}

func human(ev any) string {
	switch v := ev.(type) {
	case List:
		return fmt.Sprintf("List: %d entries, %d unique, %d duplicates\n", v.Entries, v.Unique, v.Duplicated)
	case Progress:
		if v.Phase == "scan" {
			return fmt.Sprintf("\rScanning: %d files checked, %d matched   ", v.Checked, v.Matched)
		}
		return fmt.Sprintf("\rExporting: %d/%d files, %s/%s   ", v.Files, v.TotalFiles, Size(v.Bytes), Size(v.TotalBytes))
	case Preview:
		s := fmt.Sprintf("\nFound %d matching files (%s) in %d checked. %d names found nothing.\n",
			v.Matched, Size(v.TotalBytes), v.Checked, v.NotFound)
		for i, f := range v.Files {
			if i == 20 {
				s += fmt.Sprintf("  ... and %d more\n", v.Matched-20)
				break
			}
			s += "  " + f.Path + "\n"
		}
		return s
	case Done:
		s := fmt.Sprintf("\nDone in %.1fs.", float64(v.ElapsedMs)/1000)
		if v.Output != "" {
			s += fmt.Sprintf(" Exported %d files (%s) to %s", v.Exported, Size(v.Bytes), v.Output)
		}
		for _, r := range v.Reports {
			s += "\n  report: " + r
		}
		for _, w := range v.Warnings {
			s += "\n  warning: " + w
		}
		return s + "\n"
	case TreeProgress:
		return fmt.Sprintf("\r%s: %d files, %s   ", v.Phase, v.Files, Size(v.Bytes))
	case TreeDone:
		return fmt.Sprintf("\nListed %d files in %d folders (%s) in %.1fs. %d reused, %d cloud-only skipped, %d ignored, %d unreadable.\n",
			v.Files, v.Dirs, Size(v.Bytes), float64(v.ElapsedMs)/1000, v.Reused, v.Cloud, v.Ignored, v.Errors)
	case HashDone:
		return fmt.Sprintf("\nHashed %d files (%s) in %.1fs.\n", v.Files, Size(v.Bytes), float64(v.ElapsedMs)/1000)
	case Error:
		return "\nError: " + v.Message + "\n"
	case Cancelled:
		return "\nCancelled.\n"
	}
	return ""
}

// Size formats bytes as "1.5 GB".
func Size(n int64) string {
	units := []string{"B", "KB", "MB", "GB", "TB"}
	f := float64(n)
	i := 0
	for f >= 1024 && i < len(units)-1 {
		f /= 1024
		i++
	}
	if i == 0 {
		return fmt.Sprintf("%d B", n)
	}
	return fmt.Sprintf("%.1f %s", f, units[i])
}

// TreeProgress: sent while `tree` or `hash` runs.
type TreeProgress struct {
	Type       string `json:"type"`  // "progress"
	Phase      string `json:"phase"` // "tree" | "hash"
	Files      int64  `json:"files"`
	Dirs       int64  `json:"dirs,omitempty"`
	Bytes      int64  `json:"bytes"`
	TotalFiles int64  `json:"totalFiles,omitempty"`
}

// TreeDone: the `tree` command finished (the directory records came before it as "dir" events).
type TreeDone struct {
	Type      string `json:"type"` // "done"
	Command   string `json:"command"`
	Files     int64  `json:"files"`
	Dirs      int64  `json:"dirs"`
	Bytes     int64  `json:"bytes"`
	Errors    int64  `json:"errors"`
	Reused    int64  `json:"reused"`
	Cloud     int64  `json:"cloud"`   // cloud placeholder files skipped
	Ignored   int64  `json:"ignored"` // entries skipped by ignore rules
	ElapsedMs int64  `json:"elapsedMs"`
}

// Hashes: a batch of checksums; "" means the file could not be read.
type Hashes struct {
	Type string            `json:"type"` // "hashes"
	H    map[string]string `json:"h"`
}

// HashDone: the `hash` command finished.
type HashDone struct {
	Type      string `json:"type"` // "done"
	Command   string `json:"command"`
	Files     int64  `json:"files"`
	Bytes     int64  `json:"bytes"`
	ElapsedMs int64  `json:"elapsedMs"`
}
