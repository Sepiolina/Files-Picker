package tree

import (
	"context"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"testing"
	"time"

	"filepicker/engine/internal/ignore"
)

func write(t *testing.T, root, rel, data string) {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
}

// ageDirs back-dates every directory's mtime so that a file created later always changes it,
// even on filesystems with coarse timestamps.
func ageDirs(t *testing.T, root string) {
	t.Helper()
	past := time.Now().Add(-time.Hour)
	var dirs []string
	filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err == nil && d.IsDir() {
			dirs = append(dirs, p)
		}
		return nil
	})
	for _, d := range dirs {
		if err := os.Chtimes(d, past, past); err != nil {
			t.Fatal(err)
		}
	}
}

func walk(t *testing.T, opt Options) (map[string]*Dir, *Counters) {
	t.Helper()
	got := map[string]*Dir{}
	var c Counters
	if err := Walk(context.Background(), opt, &c, func(d *Dir) { got[d.Rel] = d }); err != nil {
		t.Fatal(err)
	}
	return got, &c
}

func names(dirs map[string]*Dir) []string {
	var out []string
	for rel, d := range dirs {
		for _, f := range d.F {
			if rel != "" {
				out = append(out, rel+"/"+f.Name)
			} else {
				out = append(out, f.Name)
			}
		}
	}
	sort.Strings(out)
	return out
}

func TestWalkFiltersAndCounts(t *testing.T) {
	root := t.TempDir()
	write(t, root, "a.txt", "12345")
	write(t, root, "sub/b.txt", "1")
	write(t, root, ".hidden/c.txt", "1")
	write(t, root, "node_modules/x/d.js", "1")
	write(t, root, "sub/skip.tmp", "1")
	got, c := walk(t, Options{Root: root, SkipHidden: true, Ignore: ignore.Parse("node_modules/\n*.tmp")})
	want := []string{"a.txt", "sub/b.txt"}
	if g := names(got); len(g) != 2 || g[0] != want[0] || g[1] != want[1] {
		t.Fatalf("files = %v, want %v", g, want)
	}
	if c.Files.Load() != 2 || c.Bytes.Load() != 6 || c.Ignored.Load() != 2 {
		t.Fatalf("counters files=%d bytes=%d ignored=%d", c.Files.Load(), c.Bytes.Load(), c.Ignored.Load())
	}
	all, _ := walk(t, Options{Root: root})
	if len(names(all)) != 5 {
		t.Fatalf("without filters expected 5 files, got %v", names(all))
	}
}

func TestKnownDirsAreReused(t *testing.T) {
	root := t.TempDir()
	write(t, root, "a/one.txt", "1")
	write(t, root, "b/two.txt", "1")
	ageDirs(t, root)
	first, _ := walk(t, Options{Root: root})
	known := map[string]Known{}
	for rel, d := range first {
		known[rel] = Known{d.M, d.D}
	}
	again, c := walk(t, Options{Root: root, Known: known})
	if c.Reused.Load() != int64(len(first)) {
		t.Fatalf("expected all %d dirs reused, got %d", len(first), c.Reused.Load())
	}
	for _, d := range again {
		if !d.Same {
			t.Fatalf("dir %q should be marked same", d.Rel)
		}
	}
	write(t, root, "a/new.txt", "1") // changes a's mtime only
	third, c := walk(t, Options{Root: root, Known: known})
	if third["a"].Same || !third["b"].Same || len(third["a"].F) != 2 {
		t.Fatalf("a must be re-listed, b reused: %+v", third["a"])
	}
	_ = c
}

func TestCancel(t *testing.T) {
	root := t.TempDir()
	write(t, root, "a/b.txt", "1")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	var c Counters
	if err := Walk(ctx, Options{Root: root}, &c, func(*Dir) {}); err == nil {
		t.Fatal("expected a cancellation error")
	}
}

func TestNoDirTimeListsTheSameFilesWithoutFolderTimes(t *testing.T) {
	root := t.TempDir()
	write(t, root, "a.txt", "1")
	write(t, root, "sub/b.txt", "22")
	write(t, root, "sub/deep/c.txt", "333")

	full, _ := walk(t, Options{Root: root})
	fast, c := walk(t, Options{Root: root, NoDirTime: true})
	if got, want := names(fast), names(full); len(got) != 3 || !equal(got, want) {
		t.Fatalf("files differ: %v vs %v", got, want)
	}
	for rel, d := range fast {
		if d.M != 0 {
			t.Errorf("%q: folder time should be skipped, got %d", rel, d.M)
		}
	}
	if c.Dirs.Load() != 3 || c.Files.Load() != 3 {
		t.Fatalf("counts: %d dirs, %d files", c.Dirs.Load(), c.Files.Load())
	}
}

func equal(a, b []string) bool {
	sort.Strings(a)
	sort.Strings(b)
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
