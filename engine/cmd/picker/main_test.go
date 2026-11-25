package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// End-to-end: build a small tree, run "export" exactly as the GUI does, and
// check the archive keeps the sub-folder structure.
func TestExportZipEndToEnd(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "src")
	files := []string{"gg_abc_.txt", "a/dq122130219_abc_1291291.txt", "a/b/gg_abcde_.txt", "c/HWUDS_1.pdf", "c/other.txt"}
	for _, f := range files {
		p := filepath.Join(src, filepath.FromSlash(f))
		os.MkdirAll(filepath.Dir(p), 0o755)
		os.WriteFile(p, []byte("data:"+f), 0o644)
	}
	out := filepath.Join(dir, "out.zip")
	jobJSON, _ := json.Marshal(map[string]any{
		"source": src,
		"list":   map[string]any{"text": "abc\nHWUDS\nnothere"},
		"output": map[string]any{"type": "zip", "path": out, "report": true},
	})

	var stdout bytes.Buffer
	code := run([]string{"export"}, strings.NewReader(string(jobJSON)+"\n"), &stdout)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stdout.String())
	}

	types := map[string]map[string]any{}
	sc := bufio.NewScanner(&stdout)
	sc.Buffer(nil, 1<<24)
	for sc.Scan() {
		var ev map[string]any
		json.Unmarshal(sc.Bytes(), &ev)
		types[ev["type"].(string)] = ev
	}
	if types["preview"]["notFound"].(float64) != 1 {
		t.Errorf("expected 1 name not found: %v", types["preview"])
	}

	zr, err := zip.OpenReader(out)
	if err != nil {
		t.Fatal(err)
	}
	defer zr.Close()
	var got []string
	for _, f := range zr.File {
		got = append(got, f.Name)
	}
	sort.Strings(got)
	want := []string{"a/dq122130219_abc_1291291.txt", "c/HWUDS_1.pdf", "gg_abc_.txt"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("zip contents %v, want %v", got, want)
	}
	for _, r := range []string{"out_files.csv", "out_names.csv"} {
		if _, err := os.Stat(filepath.Join(dir, r)); err != nil {
			t.Errorf("missing report %s", r)
		}
	}
	if _, err := os.Stat(out + ".partial"); !os.IsNotExist(err) {
		t.Error("partial file left behind")
	}
}

func TestCancelViaStdin(t *testing.T) {
	jobJSON := `{"source":"` + filepath.ToSlash(t.TempDir()) + `","list":{"text":"x"}}`
	var stdout bytes.Buffer
	// stdin closes right after the job -> with --stdin-control this cancels
	code := run([]string{"scan", "--stdin-control"}, strings.NewReader(jobJSON+"\ncancel\n"), &stdout)
	if code != 0 && code != 130 {
		t.Fatalf("unexpected exit %d: %s", code, stdout.String())
	}
}
