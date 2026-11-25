// Package tree lists a whole folder tree, one record per directory, in parallel.
//
// It is the engine behind the GUI's Compare, Snapshots, Find, Duplicates and
// Space tabs. A directory is read by a pool of workers; each result is handed to
// emit as a Dir. Only names, sizes and modified times are read - never file contents.
//
// Known directories: when a directory's modified time equals the one in
// Options.Known, it is not listed again - its saved sub-folder names are reused
// (Dir.Same = true) and the caller keeps its saved file list. Adding, removing or
// renaming an entry changes the folder's own mtime, so this is what makes
// refreshing a large cached index take seconds instead of minutes.
package tree

import (
	"context"
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"

	"filepicker/engine/internal/fsattr"
	"filepicker/engine/internal/ignore"
)

// Known is what the caller remembers about one directory from an earlier scan.
type Known struct {
	M int64    `json:"m"`
	D []string `json:"d"`
}

type Options struct {
	Root       string
	SkipHidden bool // hidden files and well-known system folders
	SkipCloud  bool // cloud placeholder files (OneDrive / iCloud online-only)
	Ignore     *ignore.Matcher
	Known      map[string]Known // by relative dir path ("" = root)
	Workers    int              // concurrent directory reads; 0 = auto
}

// File is serialised as [name, size, mtimeNs].
type File struct {
	Name string
	Size int64
	M    int64
}

func (f File) MarshalJSON() ([]byte, error) {
	name, _ := json.Marshal(f.Name)
	b := make([]byte, 0, len(name)+40)
	b = append(b, '[')
	b = append(b, name...)
	b = append(b, ',')
	b = strconv.AppendInt(b, f.Size, 10)
	b = append(b, ',')
	b = strconv.AppendInt(b, f.M, 10)
	return append(b, ']'), nil
}

// Dir is one directory record - also the JSON event the GUI reads.
type Dir struct {
	Type string   `json:"type"` // "dir"
	Rel  string   `json:"rel"`  // "" for the root, forward slashes
	M    int64    `json:"m"`    // the directory's own mtime (ns)
	Same bool     `json:"same,omitempty"`
	F    []File   `json:"f"`
	D    []string `json:"d"` // sub-folder names
}

type Counters struct {
	Files, Dirs, Bytes, Errors, Reused, Cloud, Ignored atomic.Int64
}

var systemAny = map[string]bool{"$recycle.bin": true, "system volume information": true, ".trash": true,
	".trashes": true, ".spotlight-v100": true, ".fseventsd": true}
var systemTop = map[string]bool{"$recycle.bin": true, "system volume information": true, "windows": true,
	"recovery": true, "$windows.~bt": true, "proc": true, "sys": true, "dev": true, "run": true, "snap": true,
	"lost+found": true, "system": true, "library": true, "private": true, "cores": true}

// Walk lists opt.Root and calls emit (from a single goroutine) for every directory.
func Walk(ctx context.Context, opt Options, c *Counters, emit func(*Dir)) error {
	workers := opt.Workers
	if workers <= 0 {
		workers = min(32, max(8, runtime.NumCPU()*2))
	}
	q := newQueue()
	q.push("")
	out := make(chan *Dir, 512)
	done := make(chan struct{})
	go func() {
		for d := range out {
			emit(d)
		}
		close(done)
	}()
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				rel, ok := q.pop()
				if !ok {
					return
				}
				if ctx.Err() == nil {
					if rec := readDir(opt, rel, c); rec != nil {
						out <- rec
						q.push(joinAll(rel, rec.D)...)
					}
				}
				q.finish()
			}
		}()
	}
	wg.Wait()
	close(out)
	<-done
	return ctx.Err()
}

func readDir(opt Options, rel string, c *Counters) *Dir {
	path := opt.Root
	if rel != "" {
		path = filepath.Join(opt.Root, filepath.FromSlash(rel))
	}
	st, err := os.Stat(path)
	if err != nil {
		c.Errors.Add(1)
		return nil
	}
	m := st.ModTime().UnixNano()
	c.Dirs.Add(1)
	if k, ok := opt.Known[rel]; ok && k.M == m {
		c.Reused.Add(1)
		return &Dir{Type: "dir", Rel: rel, M: m, Same: true, F: []File{}, D: k.D}
	}
	entries, err := os.ReadDir(path)
	if err != nil {
		c.Errors.Add(1)
	}
	rec := &Dir{Type: "dir", Rel: rel, M: m, F: []File{}, D: []string{}}
	for _, e := range entries {
		name := e.Name()
		if e.Type()&fs.ModeSymlink != 0 {
			continue
		}
		isDir := e.IsDir()
		child := name
		if rel != "" {
			child = rel + "/" + name
		}
		if opt.Ignore.Match(child, isDir) {
			c.Ignored.Add(1)
			continue
		}
		info, err := e.Info()
		if err != nil {
			c.Errors.Add(1)
			continue
		}
		if opt.SkipHidden {
			low := strings.ToLower(name)
			if fsattr.Hidden(name, info) || systemAny[low] || (rel == "" && systemTop[low]) {
				continue
			}
		}
		switch {
		case isDir:
			rec.D = append(rec.D, name)
		case info.Mode().IsRegular():
			if opt.SkipCloud && fsattr.CloudOnly(name, info) {
				c.Cloud.Add(1)
				continue
			}
			rec.F = append(rec.F, File{name, info.Size(), info.ModTime().UnixNano()})
			c.Files.Add(1)
			c.Bytes.Add(info.Size())
		}
	}
	return rec
}

func joinAll(rel string, names []string) []string {
	out := make([]string, len(names))
	for i, n := range names {
		if rel == "" {
			out[i] = n
		} else {
			out[i] = rel + "/" + n
		}
	}
	return out
}

// queue is a work list that knows when everything - including work that is still
// being produced by running workers - is finished.
type queue struct {
	mu      sync.Mutex
	cond    *sync.Cond
	items   []string
	pending int // queued + being processed
}

func newQueue() *queue { q := &queue{}; q.cond = sync.NewCond(&q.mu); return q }

func (q *queue) push(rels ...string) {
	if len(rels) == 0 {
		return
	}
	q.mu.Lock()
	q.items = append(q.items, rels...)
	q.pending += len(rels)
	q.mu.Unlock()
	q.cond.Broadcast()
}

func (q *queue) pop() (string, bool) {
	q.mu.Lock()
	defer q.mu.Unlock()
	for len(q.items) == 0 {
		if q.pending == 0 {
			return "", false
		}
		q.cond.Wait()
	}
	n := len(q.items) - 1
	rel := q.items[n]
	q.items = q.items[:n]
	return rel, true
}

func (q *queue) finish() {
	q.mu.Lock()
	q.pending--
	last := q.pending == 0
	q.mu.Unlock()
	if last {
		q.cond.Broadcast()
	}
}
