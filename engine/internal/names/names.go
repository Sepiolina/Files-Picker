// Package names turns the user's raw list (pasted text or a .txt/.csv file)
// into a clean, de-duplicated set of names to search for.
package names

import (
	"encoding/csv"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"filepicker/engine/internal/job"
)

// Name is one unique search string.
type Name struct {
	Key       string   // what is compared against file names (lower-cased if case-insensitive)
	Text      string   // how it is shown to the user
	Spellings []string // every spelling seen in the list (len > 1 means it was duplicated)
}

// Set is the de-duplicated list, in the order names first appeared.
type Set struct {
	Names   []Name
	Entries int // raw entry count before de-duplication
}

// Duplicated returns how many entries were dropped as duplicates.
func (s *Set) Duplicated() int { return s.Entries - len(s.Names) }

// Load reads the list text (from the job or from list.file) and splits it.
func Load(l job.List) ([]string, error) {
	text := l.Text
	if text == "" && l.File != "" {
		data, err := os.ReadFile(l.File)
		if err != nil {
			return nil, fmt.Errorf("cannot read list file: %w", err)
		}
		text = decodeText(data)
	}
	return Split(text, l)
}

// Split breaks raw text into entries using the list settings.
func Split(text string, l job.List) ([]string, error) {
	text = strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")

	rows, err := splitRows(text, l)
	if err != nil {
		return nil, err
	}

	// Header row + optional column selection.
	col := -1
	if l.Header && len(rows) > 0 {
		header := rows[0]
		rows = rows[1:]
		if c := strings.TrimSpace(l.Column); c != "" && !isNumber(c) {
			for i, h := range header {
				if strings.TrimSpace(h) == c {
					col = i
				}
			}
			if col < 0 {
				return nil, fmt.Errorf("column %q not found in header %v", c, header)
			}
		}
	}
	if c := strings.TrimSpace(l.Column); isNumber(c) {
		n, _ := strconv.Atoi(c)
		if n < 1 {
			return nil, fmt.Errorf("column numbers start at 1")
		}
		col = n - 1
	}

	var out []string
	for _, row := range rows {
		cells := row
		if col >= 0 {
			if col >= len(row) {
				continue
			}
			cells = row[col : col+1]
		}
		for _, c := range cells {
			if l.Trim {
				c = strings.TrimSpace(c)
			}
			if c != "" {
				out = append(out, c)
			}
		}
	}
	return out, nil
}

func splitRows(text string, l job.List) ([][]string, error) {
	lines := strings.Split(text, "\n")
	switch l.Separator {
	case "newline":
		rows := make([][]string, len(lines))
		for i, line := range lines {
			rows[i] = []string{line}
		}
		return rows, nil
	case "whitespace":
		rows := make([][]string, len(lines))
		for i, line := range lines {
			rows[i] = strings.Fields(line)
		}
		return rows, nil
	}

	sep := map[string]string{"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}[l.Separator]
	if l.Separator == "custom" {
		sep = strings.ReplaceAll(l.Custom, `\t`, "\t")
	}
	if sep == "" {
		return splitRows(text, job.List{Separator: "newline"})
	}

	// Single-character separator + quotes: use a real CSV reader.
	if l.CSVQuotes && len([]rune(sep)) == 1 {
		r := csv.NewReader(strings.NewReader(text))
		r.Comma = []rune(sep)[0]
		r.FieldsPerRecord = -1
		r.LazyQuotes = true
		var rows [][]string
		for {
			rec, err := r.Read()
			if err == io.EOF {
				return rows, nil
			}
			if err != nil {
				return nil, fmt.Errorf("CSV problem: %w", err)
			}
			rows = append(rows, rec)
		}
	}

	rows := make([][]string, len(lines))
	for i, line := range lines {
		rows[i] = strings.Split(line, sep)
	}
	return rows, nil
}

// Build de-duplicates entries. With caseSensitive=false "ABC", "AbC" and "abc"
// become one name. rule decides which spelling is shown: first | last | error.
func Build(entries []string, caseSensitive bool, rule string) (*Set, error) {
	set := &Set{Entries: len(entries)}
	index := map[string]int{}
	for _, e := range entries {
		key := e
		if !caseSensitive {
			key = strings.ToLower(e)
		}
		if i, seen := index[key]; seen {
			n := &set.Names[i]
			n.Spellings = append(n.Spellings, e)
			if rule == "last" {
				n.Text = e
			}
			continue
		}
		index[key] = len(set.Names)
		set.Names = append(set.Names, Name{Key: key, Text: e, Spellings: []string{e}})
	}
	if rule == "error" && set.Duplicated() > 0 {
		return set, fmt.Errorf("the list contains %d duplicate entries", set.Duplicated())
	}
	return set, nil
}

// decodeText handles UTF-8 (with/without BOM) and UTF-16 files, which is what
// Excel and Notepad typically produce.
func decodeText(b []byte) string {
	switch {
	case len(b) >= 3 && b[0] == 0xEF && b[1] == 0xBB && b[2] == 0xBF:
		return string(b[3:])
	case len(b) >= 2 && b[0] == 0xFF && b[1] == 0xFE:
		return utf16ToString(b[2:], false)
	case len(b) >= 2 && b[0] == 0xFE && b[1] == 0xFF:
		return utf16ToString(b[2:], true)
	}
	return string(b)
}

func utf16ToString(b []byte, bigEndian bool) string {
	var sb strings.Builder
	for i := 0; i+1 < len(b); i += 2 {
		u := uint16(b[i]) | uint16(b[i+1])<<8
		if bigEndian {
			u = uint16(b[i])<<8 | uint16(b[i+1])
		}
		sb.WriteRune(rune(u)) // BMP is plenty for file-name lists
	}
	return sb.String()
}

func isNumber(s string) bool {
	_, err := strconv.Atoi(s)
	return s != "" && err == nil
}
