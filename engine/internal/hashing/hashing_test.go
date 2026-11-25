package hashing

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestHashes(t *testing.T) {
	dir := t.TempDir()
	for name, data := range map[string]string{"a": "hello world", "b": "hello world", "c": "hello there", "long1": "0123456789-A", "long2": "0123456789-B"} {
		os.WriteFile(filepath.Join(dir, name), []byte(data), 0o644)
	}
	ctx := context.Background()
	sum := func(name string, partial int64) string {
		s, err := File(ctx, filepath.Join(dir, name), partial, nil)
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	if sum("a", 0) != sum("b", 0) || sum("a", 0) == sum("c", 0) {
		t.Fatal("full hashes wrong")
	}
	if sum("long1", 10) != sum("long2", 10) || sum("long1", 0) == sum("long2", 0) {
		t.Fatal("partial hashing wrong")
	}
	if len(sum("a", 0)) != 32 {
		t.Fatal("hash must be 32 hex chars")
	}
	// known value: sha256("hello world") starts b94d27b9934d3e08a52e52d7da7dabfa
	if sum("a", 0) != "b94d27b9934d3e08a52e52d7da7dabfa" {
		t.Fatalf("unexpected digest %s", sum("a", 0))
	}
	var c Counters
	got := map[string]string{}
	err := Run(ctx, Options{Root: dir, Files: []string{"a", "b", "missing"}}, &c, func(r Result) { got[r.Rel] = r.Sum })
	if err != nil || got["a"] != got["b"] || got["missing"] != "" || c.Files.Load() != 3 {
		t.Fatalf("Run: %v %v", err, got)
	}
}
