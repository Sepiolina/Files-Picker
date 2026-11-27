// Package hashing computes content checksums in parallel.
//
// The checksum is SHA-256 shortened to its first 16 bytes (32 hex characters).
// "Partial" hashes read only the first N bytes - enough to rule out most
// non-identical files cheaply before a duplicate search reads whole files.
// The Python fallback (fscore.file_hash) produces identical values.
package hashing

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// bufPool avoids allocating a fresh 1 MB buffer for every file.
var bufPool = sync.Pool{New: func() any { b := make([]byte, 1<<20); return &b }}

type Options struct {
	Root    string
	Files   []string // relative paths, forward slashes
	Partial int64    // >0: hash only the first Partial bytes
	Workers int      // 0 = 4
}

type Counters struct {
	Files, Bytes atomic.Int64
}

// Result: Sum is "" when the file could not be read.
type Result struct {
	Rel string
	Sum string
}

func File(ctx context.Context, path string, partial int64, c *Counters) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	var r io.Reader = f
	if partial > 0 {
		r = io.LimitReader(f, partial)
	}
	bp := bufPool.Get().(*[]byte)
	defer bufPool.Put(bp)
	buf := *bp
	for {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		n, err := r.Read(buf)
		if n > 0 {
			h.Write(buf[:n])
			if c != nil {
				c.Bytes.Add(int64(n))
			}
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", err
		}
	}
	return hex.EncodeToString(h.Sum(nil)[:16]), nil
}

// Run hashes opt.Files with a worker pool and calls emit (from one goroutine) for each result.
func Run(ctx context.Context, opt Options, c *Counters, emit func(Result)) error {
	workers := opt.Workers
	if workers <= 0 {
		workers = 4
	}
	jobs := make(chan string)
	results := make(chan Result, 256)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for rel := range jobs {
				sum, err := File(ctx, filepath.Join(opt.Root, filepath.FromSlash(rel)), opt.Partial, c)
				if err != nil {
					sum = ""
				}
				c.Files.Add(1)
				results <- Result{rel, sum}
			}
		}()
	}
	go func() {
		defer close(jobs)
		for _, rel := range opt.Files {
			select {
			case jobs <- rel:
			case <-ctx.Done():
				return
			}
		}
	}()
	go func() { wg.Wait(); close(results) }()
	// A worker blocked reading a hung file cannot be interrupted. After a cancel,
	// stop waiting for it once the grace period is over.
	var grace <-chan time.Time
	cancelled := ctx.Done()
	for {
		select {
		case r, ok := <-results:
			if !ok {
				return ctx.Err()
			}
			emit(r)
		case <-cancelled:
			cancelled = nil // fire once
			grace = time.After(CancelGrace)
		case <-grace:
			return context.Canceled
		}
	}
}

// CancelGrace is how long Run waits for blocked workers after a cancel.
var CancelGrace = 3 * time.Second
