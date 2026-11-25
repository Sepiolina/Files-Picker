// Package job defines the "job" - every setting needed for one run.
//
// The GUI builds a job, serialises it to JSON and hands it to the engine.
// The same JSON can be saved to a file and run from the command line, so a
// job file is also the format used for "run again" from history.
package job

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

type Job struct {
	Source       string `json:"source"`       // folder to search
	Recursive    bool   `json:"recursive"`    // include sub-folders
	List         List   `json:"list"`         // the names to look for
	Match        Match  `json:"match"`        // how names are compared to files
	Output       Output `json:"output"`       // where/how matched files go
	PreviewLimit int    `json:"previewLimit"` // max file rows sent back for preview
}

type List struct {
	Text      string `json:"text"`      // names pasted directly
	File      string `json:"file"`      // ...or a .txt/.csv path (used when Text is empty)
	Separator string `json:"separator"` // newline | comma | semicolon | tab | pipe | whitespace | custom
	Custom    string `json:"custom"`    // separator when Separator == "custom"
	CSVQuotes bool   `json:"csvQuotes"` // honour "quoted, values"
	Header    bool   `json:"header"`    // first row is a header
	Column    string `json:"column"`    // 1-based number or header name; empty = all columns
	Trim      bool   `json:"trim"`      // trim spaces around each name
}

type Match struct {
	Mode          string   `json:"mode"`          // contains | whole | exact | prefix | suffix
	Target        string   `json:"target"`        // stem (name w/o extension) | name | path
	CaseSensitive bool     `json:"caseSensitive"` //
	Duplicates    string   `json:"duplicates"`    // first | last | error
	Overlap       string   `json:"overlap"`       // longest | shortest | first | all
	Extensions    []string `json:"extensions"`    // e.g. ["pdf", ".jpg"]; empty = all
}

type Output struct {
	Type        string `json:"type"`        // zip | folder | tar | targz
	Path        string `json:"path"`        // archive file or destination folder
	GroupByName bool   `json:"groupByName"` // put each file under a folder named after its matched name
	Report      bool   `json:"report"`      // write CSV reports next to the output
	Compression string `json:"compression"` // zip only: store | deflate
	Conflict    string `json:"conflict"`    // folder only: rename | overwrite | skip
}

// Default returns a job with sensible defaults; JSON input is laid on top.
func Default() Job {
	return Job{
		Recursive:    true,
		List:         List{Separator: "newline", CSVQuotes: true, Trim: true},
		Match:        Match{Mode: "whole", Target: "stem", Duplicates: "first", Overlap: "longest"},
		Output:       Output{Type: "zip", Report: true, Compression: "store", Conflict: "rename"},
		PreviewLimit: 5000,
	}
}

// Parse decodes JSON over the defaults and validates the result.
func Parse(data []byte) (Job, error) {
	j := Default()
	if err := json.Unmarshal(data, &j); err != nil {
		return j, fmt.Errorf("job is not valid JSON: %w", err)
	}
	return j, j.Validate()
}

// Load reads a job file from disk.
func Load(path string) (Job, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Job{}, err
	}
	return Parse(data)
}

func (j Job) Validate() error {
	checks := []struct{ field, value, allowed string }{
		{"list.separator", j.List.Separator, "newline comma semicolon tab pipe whitespace custom"},
		{"match.mode", j.Match.Mode, "contains whole exact prefix suffix"},
		{"match.target", j.Match.Target, "stem name path"},
		{"match.duplicates", j.Match.Duplicates, "first last error"},
		{"match.overlap", j.Match.Overlap, "longest shortest first all"},
		{"output.type", j.Output.Type, "zip folder tar targz"},
		{"output.compression", j.Output.Compression, "store deflate"},
		{"output.conflict", j.Output.Conflict, "rename overwrite skip"},
	}
	for _, c := range checks {
		if !contains(strings.Fields(c.allowed), c.value) {
			return fmt.Errorf("%s must be one of [%s], got %q", c.field, c.allowed, c.value)
		}
	}
	return nil
}

// ExtensionSet normalises ["PDF", ".jpg"] to {".pdf", ".jpg"}. Nil means "all".
func (m Match) ExtensionSet() map[string]bool {
	if len(m.Extensions) == 0 {
		return nil
	}
	set := map[string]bool{}
	for _, e := range m.Extensions {
		e = strings.ToLower(strings.TrimSpace(e))
		if e == "" {
			continue
		}
		if !strings.HasPrefix(e, ".") {
			e = "." + e
		}
		set[e] = true
	}
	if len(set) == 0 {
		return nil
	}
	return set
}

func contains(list []string, v string) bool {
	for _, s := range list {
		if s == v {
			return true
		}
	}
	return false
}
