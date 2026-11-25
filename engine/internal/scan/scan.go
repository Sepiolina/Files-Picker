// Package scan walks a folder tree in parallel and collects matching files.
//
// Each directory is read in its own goroutine; a semaphore limits how many
// directories are read at the same time so we don't overwhelm the disk.
// Only file *names* are inspected - file contents are never read here.
package scan

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"

	"filepicker/engine/internal/match"
	"filepicker/engine/internal/names"
)

type Options struct {
	Root       string
	Recursive  bool
	Target     string          // stem | name | path
	Extensions map[string]bool // nil = all
	Overlap    string          // longest | shortest | first | all
	Exclude    []string        // absolute paths to skip (our own output)
	Workers    int             // concurrent directory reads; 0 = auto
}

type File struct {
	Rel      string // relative path with forward slashes, e.g. "a/b/x_abc_1.txt"
	Abs      string // absolute path on disk
	Size     int64
	Matched  []int // every name found in this file (positions in names.Set)
	Assigned []int // the name(s) it belongs to after the overlap rule
}

type Result struct {
	Files    []File
	Checked  int64    // files looked at
	Warnings []string // unreadable folders etc.
}

// Counters are read by the progress reporter while the scan runs.
type Counters struct {
	Checked atomic.Int64
	Matched atomic.Int64
}

type walker struct {
	opt     Options
	set     *names.Set
	matcher *match.Matcher
	ctx     context.Context
	count   *Counters
	sem     chan struct{}
	wg      sync.WaitGroup

	mu       sync.Mutex
	files    []File
	warnings []string
}

func Run(ctx context.Context, opt Options, set *names.Set, m *match.Matcher, count *Counters) (*Result, error) {
	root, err := filepath.Abs(opt.Root)
	if err != nil {
		return nil, err
	}
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		return nil, &os.PathError{Op: "scan", Path: root, Err: os.ErrNotExist}
	}
	opt.Root = root
	if opt.Workers <= 0 {
		opt.Workers = max(4, runtime.NumCPU()*2)
	}

	w := &walker{opt: opt, set: set, matcher: m, ctx: ctx, count: count,
		sem: make(chan struct{}, opt.Workers)}
	w.wg.Add(1)
	go w.walkDir(root)
	w.wg.Wait()

	if err := ctx.Err(); err != nil {
		return nil, err
	}
	sort.Slice(w.files, func(i, j int) bool { return NaturalLess(w.files[i].Rel, w.files[j].Rel) })
	return &Result{Files: w.files, Checked: count.Checked.Load(), Warnings: w.warnings}, nil
}

func (w *walker) walkDir(dir string) {
	defer w.wg.Done()
	if w.ctx.Err() != nil {
		return
	}

	w.sem <- struct{}{} // limit concurrent disk reads
	entries, err := os.ReadDir(dir)
	<-w.sem
	if err != nil {
		w.warn("cannot read folder: " + err.Error())
		return
	}

	for _, e := range entries {
		full := filepath.Join(dir, e.Name())
		switch {
		case e.IsDir():
			if w.opt.Recursive && !w.isExcluded(full) {
				w.wg.Add(1)
				go w.walkDir(full)
			}
		case e.Type().IsRegular(): // skips symlinks, devices, etc.
			w.count.Checked.Add(1)
			w.checkFile(full, e)
		}
	}
}

func (w *walker) checkFile(full string, e os.DirEntry) {
	name := e.Name()
	ext := strings.ToLower(filepath.Ext(name))
	if w.opt.Extensions != nil && !w.opt.Extensions[ext] {
		return
	}

	rel, _ := filepath.Rel(w.opt.Root, full)
	rel = filepath.ToSlash(rel)

	var subject string
	switch w.opt.Target {
	case "name":
		subject = name
	case "path":
		subject = rel
	default: // stem
		subject = strings.TrimSuffix(name, filepath.Ext(name))
		if subject == "" { // ".bashrc" style names
			subject = name
		}
	}

	found := w.matcher.Find(subject)
	if len(found) == 0 || w.isExcluded(full) {
		return
	}
	info, err := e.Info()
	if err != nil {
		w.warn("cannot read file info: " + err.Error())
		return
	}

	w.count.Matched.Add(1)
	f := File{Rel: rel, Abs: full, Size: info.Size(), Matched: found,
		Assigned: match.Assign(found, w.set, w.opt.Overlap)}
	w.mu.Lock()
	w.files = append(w.files, f)
	w.mu.Unlock()
}

func (w *walker) isExcluded(path string) bool {
	for _, x := range w.opt.Exclude {
		if samePath(x, path) {
			return true
		}
	}
	return false
}

func (w *walker) warn(msg string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(w.warnings) < 100 {
		w.warnings = append(w.warnings, msg)
	}
}

func samePath(a, b string) bool {
	a, b = filepath.Clean(a), filepath.Clean(b)
	if runtime.GOOS == "windows" || runtime.GOOS == "darwin" {
		return strings.EqualFold(a, b) // case-insensitive file systems
	}
	return a == b
}

// NaturalLess orders names the way people expect: "file_2" before "file_10",
// ignoring case. Runs of digits are compared by numeric value.
func NaturalLess(a, b string) bool {
	a, b = strings.ToLower(a), strings.ToLower(b)
	for a != "" && b != "" {
		da, db := digitPrefix(a), digitPrefix(b)
		if da != "" && db != "" {
			na, nb := strings.TrimLeft(da, "0"), strings.TrimLeft(db, "0")
			if len(na) != len(nb) {
				return len(na) < len(nb) // fewer significant digits = smaller number
			}
			if na != nb {
				return na < nb
			}
			a, b = a[len(da):], b[len(db):]
			continue
		}
		if a[0] != b[0] {
			return a[0] < b[0]
		}
		a, b = a[1:], b[1:]
	}
	return len(a) < len(b)
}

func digitPrefix(s string) string {
	i := 0
	for i < len(s) && s[i] >= '0' && s[i] <= '9' {
		i++
	}
	return s[:i]
}
