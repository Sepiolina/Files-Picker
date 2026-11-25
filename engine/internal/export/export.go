// Package export copies matched files into a zip, tar, tar.gz or folder,
// keeping their sub-folder structure.
//
// Archives are written to "<name>.partial" first and renamed when complete,
// so a cancelled or failed run never leaves a broken archive behind.
package export

import (
	"archive/tar"
	"archive/zip"
	"compress/flate"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"

	"filepicker/engine/internal/job"
	"filepicker/engine/internal/names"
	"filepicker/engine/internal/scan"
)

// Entry is one file to export: where it comes from and where it goes.
type Entry struct {
	Src  string // absolute source path
	Size int64
	Dest string // relative destination, forward slashes
}

// Counters are read by the progress reporter while the export runs.
type Counters struct {
	Bytes atomic.Int64
	Files atomic.Int64
}

// Plan turns scan results into export entries. With groupByName each file is
// placed under a folder named after its assigned name ("ABC/a/b/file.txt").
func Plan(files []scan.File, set *names.Set, groupByName bool) []Entry {
	var out []Entry
	used := map[string]bool{}
	for _, f := range files {
		dests := []string{f.Rel}
		if groupByName {
			dests = dests[:0]
			for _, i := range f.Assigned {
				dests = append(dests, safeFolderName(set.Names[i].Text)+"/"+f.Rel)
			}
		}
		for _, d := range dests {
			if !used[d] {
				used[d] = true
				out = append(out, Entry{Src: f.Abs, Size: f.Size, Dest: d})
			}
		}
	}
	return out
}

// Run writes all entries. It returns per-file warnings (unreadable files are
// skipped, not fatal) and a fatal error if the output itself fails.
func Run(ctx context.Context, entries []Entry, out job.Output, c *Counters) ([]string, error) {
	switch out.Type {
	case "folder":
		return toFolder(ctx, entries, out, c)
	case "zip":
		return toArchive(ctx, entries, out.Path, c, func(w io.Writer) archiveWriter {
			return newZip(w, out.Compression == "deflate")
		})
	case "tar", "targz":
		return toArchive(ctx, entries, out.Path, c, func(w io.Writer) archiveWriter {
			return newTar(w, out.Type == "targz")
		})
	}
	return nil, fmt.Errorf("unknown output type %q", out.Type)
}

// ---- archives ---------------------------------------------------------------

type archiveWriter interface {
	Add(e Entry, info os.FileInfo) (io.Writer, error) // start a new file inside the archive
	Close() error
}

func toArchive(ctx context.Context, entries []Entry, dest string, c *Counters,
	open func(io.Writer) archiveWriter) ([]string, error) {

	partial := dest + ".partial"
	file, err := os.Create(partial)
	if err != nil {
		return nil, err
	}
	success := false
	defer func() {
		if !success {
			file.Close()
			os.Remove(partial)
		}
	}()

	aw := open(file)
	buf := make([]byte, 1<<20)
	var warnings []string
	for _, e := range entries {
		if err := ctx.Err(); err != nil {
			return warnings, err
		}
		if err := addOne(ctx, aw, e, buf, c); err != nil {
			if ctx.Err() != nil {
				return warnings, ctx.Err()
			}
			warnings = append(warnings, err.Error())
		}
		c.Files.Add(1)
	}
	if err := aw.Close(); err != nil {
		return warnings, err
	}
	if err := file.Close(); err != nil {
		return warnings, err
	}
	os.Remove(dest) // replace an existing archive (the GUI already asked)
	if err := os.Rename(partial, dest); err != nil {
		return warnings, err
	}
	success = true
	return warnings, nil
}

func addOne(ctx context.Context, aw archiveWriter, e Entry, buf []byte, c *Counters) error {
	src, err := os.Open(e.Src)
	if err != nil {
		return err
	}
	defer src.Close()
	info, err := src.Stat()
	if err != nil {
		return err
	}
	w, err := aw.Add(e, info)
	if err != nil {
		return err
	}
	// LimitReader keeps tar happy if the file grows while we read it.
	_, err = io.CopyBuffer(&progressWriter{ctx, w, c}, io.LimitReader(src, info.Size()), buf)
	return err
}

type zipWriter struct {
	zw     *zip.Writer
	method uint16
}

func newZip(w io.Writer, deflate bool) *zipWriter {
	zw := zip.NewWriter(w)
	method := zip.Store
	if deflate {
		method = zip.Deflate
		// BestSpeed: ~3x faster than the default for a small size cost.
		zw.RegisterCompressor(zip.Deflate, func(w io.Writer) (io.WriteCloser, error) {
			return flate.NewWriter(w, flate.BestSpeed)
		})
	}
	return &zipWriter{zw, method}
}

func (z *zipWriter) Add(e Entry, info os.FileInfo) (io.Writer, error) {
	h, err := zip.FileInfoHeader(info)
	if err != nil {
		return nil, err
	}
	h.Name, h.Method = e.Dest, z.method
	return z.zw.CreateHeader(h) // Zip64 is used automatically for >4 GB
}

func (z *zipWriter) Close() error { return z.zw.Close() }

type tarWriter struct {
	tw *tar.Writer
	gz *gzip.Writer
}

func newTar(w io.Writer, gz bool) *tarWriter {
	t := &tarWriter{}
	if gz {
		t.gz, _ = gzip.NewWriterLevel(w, gzip.BestSpeed)
		w = t.gz
	}
	t.tw = tar.NewWriter(w)
	return t
}

func (t *tarWriter) Add(e Entry, info os.FileInfo) (io.Writer, error) {
	h, err := tar.FileInfoHeader(info, "")
	if err != nil {
		return nil, err
	}
	h.Name = e.Dest
	h.Format = tar.FormatPAX // long names + unicode
	if err := t.tw.WriteHeader(h); err != nil {
		return nil, err
	}
	return t.tw, nil
}

func (t *tarWriter) Close() error {
	if err := t.tw.Close(); err != nil {
		return err
	}
	if t.gz != nil {
		return t.gz.Close()
	}
	return nil
}

// ---- folder copy ------------------------------------------------------------

// toFolder copies with a few workers in parallel - this helps a lot on SSDs
// and network drives, and does no harm on spinning disks.
func toFolder(ctx context.Context, entries []Entry, out job.Output, c *Counters) ([]string, error) {
	if err := os.MkdirAll(out.Path, 0o755); err != nil {
		return nil, err
	}
	jobs := make(chan Entry)
	var mu sync.Mutex
	var warnings []string
	var wg sync.WaitGroup

	for w := 0; w < 4; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, 1<<20)
			for e := range jobs {
				if err := copyOne(ctx, e, out, buf, c); err != nil && ctx.Err() == nil {
					mu.Lock()
					warnings = append(warnings, err.Error())
					mu.Unlock()
				}
				c.Files.Add(1)
			}
		}()
	}
	for _, e := range entries {
		if ctx.Err() != nil {
			break
		}
		jobs <- e
	}
	close(jobs)
	wg.Wait()
	return warnings, ctx.Err()
}

func copyOne(ctx context.Context, e Entry, out job.Output, buf []byte, c *Counters) error {
	target := filepath.Join(out.Path, filepath.FromSlash(e.Dest))
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	if _, err := os.Stat(target); err == nil {
		switch out.Conflict {
		case "skip":
			c.Bytes.Add(e.Size)
			return nil
		case "rename":
			target = uniqueName(target)
		}
	}

	src, err := os.Open(e.Src)
	if err != nil {
		return err
	}
	defer src.Close()
	dst, err := os.Create(target)
	if err != nil {
		return err
	}
	_, err = io.CopyBuffer(&progressWriter{ctx, dst, c}, src, buf)
	if cerr := dst.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(target) // never leave half-copied files
		return err
	}
	if info, err := src.Stat(); err == nil {
		os.Chtimes(target, info.ModTime(), info.ModTime())
	}
	return nil
}

func uniqueName(p string) string {
	ext := path.Ext(p)
	base := strings.TrimSuffix(p, ext)
	for i := 1; ; i++ {
		candidate := fmt.Sprintf("%s (%d)%s", base, i, ext)
		if _, err := os.Stat(candidate); os.IsNotExist(err) {
			return candidate
		}
	}
}

// ---- helpers ----------------------------------------------------------------

// progressWriter counts bytes and stops promptly when the run is cancelled.
type progressWriter struct {
	ctx context.Context
	w   io.Writer
	c   *Counters
}

func (p *progressWriter) Write(b []byte) (int, error) {
	if err := p.ctx.Err(); err != nil {
		return 0, err
	}
	n, err := p.w.Write(b)
	p.c.Bytes.Add(int64(n))
	return n, err
}

// safeFolderName makes a name usable as a folder on Windows, macOS and Linux.
func safeFolderName(s string) string {
	s = strings.Map(func(r rune) rune {
		if r < 32 || strings.ContainsRune(`<>:"/\|?*`, r) {
			return '_'
		}
		return r
	}, s)
	s = strings.Trim(s, " .")
	if s == "" {
		return "_"
	}
	return s
}
