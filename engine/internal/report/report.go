// Package report writes two CSV files describing a run:
//
//	<base>_files.csv  every exported file and which names it matched
//	<base>_names.csv  every name and how many files it found (0 = not found)
//
// Files start with a UTF-8 BOM so Excel opens them with the right encoding.
package report

import (
	"encoding/csv"
	"os"
	"strconv"
	"strings"

	"filepicker/engine/internal/names"
	"filepicker/engine/internal/scan"
)

// Counts returns how many files each name matched.
func Counts(files []scan.File, set *names.Set) []int {
	counts := make([]int, len(set.Names))
	for _, f := range files {
		for _, i := range f.Matched {
			counts[i]++
		}
	}
	return counts
}

func Write(base string, files []scan.File, set *names.Set) ([]string, error) {
	join := func(idx []int) string {
		parts := make([]string, len(idx))
		for i, n := range idx {
			parts[i] = set.Names[n].Text
		}
		return strings.Join(parts, " | ")
	}

	filesPath := base + "_files.csv"
	err := writeCSV(filesPath, []string{"relative_path", "size_bytes", "matched_names", "assigned_to"},
		func(add func(...string)) {
			for _, f := range files {
				add(f.Rel, strconv.FormatInt(f.Size, 10), join(f.Matched), join(f.Assigned))
			}
		})
	if err != nil {
		return nil, err
	}

	namesPath := base + "_names.csv"
	counts := Counts(files, set)
	err = writeCSV(namesPath, []string{"name", "files_found", "found", "duplicate_spellings"},
		func(add func(...string)) {
			for i, n := range set.Names {
				found := "yes"
				if counts[i] == 0 {
					found = "NO"
				}
				dups := ""
				if len(n.Spellings) > 1 {
					dups = strings.Join(n.Spellings, " | ")
				}
				add(n.Text, strconv.Itoa(counts[i]), found, dups)
			}
		})
	if err != nil {
		return nil, err
	}
	return []string{filesPath, namesPath}, nil
}

func writeCSV(path string, header []string, rows func(add func(...string))) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	f.WriteString("\uFEFF")
	w := csv.NewWriter(f)
	w.Write(header)
	rows(func(cells ...string) { w.Write(cells) })
	w.Flush()
	return w.Error()
}
