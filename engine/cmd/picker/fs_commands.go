package main

// The `tree` and `hash` commands: fast file-system listing and checksums for the
// GUI's Compare, Snapshots, Find, Duplicates and Space tabs. They have their own
// small jobs (below) instead of the name-matching Job used by scan/export.

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"filepicker/engine/internal/events"
	"filepicker/engine/internal/hashing"
	"filepicker/engine/internal/ignore"
	"filepicker/engine/internal/tree"
)

type treeJob struct {
	Root       string `json:"root"`
	SkipHidden bool   `json:"skipHidden"`
	SkipCloud  bool   `json:"skipCloud"`
	Ignore     string `json:"ignore"`    // ignore rules, one per line
	Known      string `json:"known"`     // optional path to JSON lines {"rel","m","d"} from an earlier scan
	Workers    int    `json:"workers"`   // 0 = auto
	NoDirTime  bool   `json:"noDirTime"` // don't read each folder's own mtime (faster on network drives)
}

type hashJob struct {
	Root    string   `json:"root"`
	Files   []string `json:"files"`   // relative paths, forward slashes
	Partial int64    `json:"partial"` // >0: only the first N bytes
	Workers int      `json:"workers"`
}

// runFS handles `picker tree|hash`. args are the arguments after the command name.
func runFS(command string, args []string, stdin io.Reader, stdout io.Writer) int {
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	jobPath := flags.String("job", "", "job JSON file (default: first line of stdin)")
	format := flags.String("format", "json", "output format: json | text")
	control := flags.Bool("stdin-control", false, "after the job, read 'cancel' lines from stdin")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	emit := events.NewEmitter(stdout, *format == "text")
	in := bufio.NewReaderSize(stdin, 1<<20)

	var raw []byte
	var err error
	if *jobPath != "" {
		raw, err = os.ReadFile(*jobPath)
	} else {
		var line string
		line, err = in.ReadString('\n')
		if err == io.EOF && line != "" {
			err = nil
		}
		raw = []byte(line)
	}
	if err != nil {
		emit.Emit(events.Error{Type: "error", Message: err.Error()})
		return 1
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if *control {
		ctx = watchStdin(ctx, in)
	}

	if command == "tree" {
		err = runTree(ctx, raw, emit)
	} else {
		err = runHash(ctx, raw, emit)
	}
	switch {
	case err == nil:
		return 0
	case ctx.Err() != nil:
		emit.Emit(events.Cancelled{Type: "cancelled"})
		return 130
	default:
		emit.Emit(events.Error{Type: "error", Message: err.Error()})
		return 1
	}
}

func runTree(ctx context.Context, raw []byte, emit *events.Emitter) error {
	var j treeJob
	if err := json.Unmarshal(raw, &j); err != nil {
		return fmt.Errorf("bad job: %w", err)
	}
	if st, err := os.Stat(j.Root); err != nil || !st.IsDir() {
		return fmt.Errorf("%q is not a folder", j.Root)
	}
	opt := tree.Options{Root: j.Root, SkipHidden: j.SkipHidden, SkipCloud: j.SkipCloud,
		Ignore: ignore.Parse(j.Ignore), Workers: j.Workers, NoDirTime: j.NoDirTime}
	if j.Known != "" {
		known, err := loadKnown(j.Known)
		if err != nil {
			return fmt.Errorf("could not read the known-folders file: %w", err)
		}
		opt.Known = known
	}
	started := time.Now()
	var c tree.Counters
	stopTicker := every(150*time.Millisecond, func() {
		emit.Emit(events.TreeProgress{Type: "progress", Phase: "tree",
			Files: c.Files.Load(), Dirs: c.Dirs.Load(), Bytes: c.Bytes.Load()})
	})
	err := tree.Walk(ctx, opt, &c, func(d *tree.Dir) { emit.Emit(d) })
	stopTicker()
	if err != nil {
		return err
	}
	emit.Emit(events.TreeDone{Type: "done", Command: "tree", Files: c.Files.Load(), Dirs: c.Dirs.Load(),
		Bytes: c.Bytes.Load(), Errors: c.Errors.Load(), Reused: c.Reused.Load(), Cloud: c.Cloud.Load(),
		Ignored: c.Ignored.Load(), ElapsedMs: time.Since(started).Milliseconds()})
	return nil
}

func loadKnown(path string) (map[string]tree.Known, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	out := map[string]tree.Known{}
	r := bufio.NewReaderSize(f, 1<<20)
	for {
		line, err := r.ReadBytes('\n')
		if len(line) > 1 {
			var rec struct {
				Rel string   `json:"rel"`
				M   int64    `json:"m"`
				D   []string `json:"d"`
			}
			if json.Unmarshal(line, &rec) == nil {
				out[rec.Rel] = tree.Known{M: rec.M, D: rec.D}
			}
		}
		if err != nil {
			return out, nil
		}
	}
}

func runHash(ctx context.Context, raw []byte, emit *events.Emitter) error {
	var j hashJob
	if err := json.Unmarshal(raw, &j); err != nil {
		return fmt.Errorf("bad job: %w", err)
	}
	started := time.Now()
	var c hashing.Counters
	total := int64(len(j.Files))
	stopTicker := every(150*time.Millisecond, func() {
		emit.Emit(events.TreeProgress{Type: "progress", Phase: "hash", Files: c.Files.Load(),
			Bytes: c.Bytes.Load(), TotalFiles: total})
	})
	batch := events.Hashes{Type: "hashes", H: map[string]string{}}
	flush := func() {
		if len(batch.H) > 0 {
			emit.Emit(batch)
			batch = events.Hashes{Type: "hashes", H: map[string]string{}}
		}
	}
	err := hashing.Run(ctx, hashing.Options{Root: j.Root, Files: j.Files, Partial: j.Partial, Workers: j.Workers}, &c,
		func(r hashing.Result) {
			batch.H[r.Rel] = r.Sum
			if len(batch.H) >= 200 {
				flush()
			}
		})
	flush()
	stopTicker()
	if err != nil {
		return err
	}
	emit.Emit(events.HashDone{Type: "done", Command: "hash", Files: c.Files.Load(), Bytes: c.Bytes.Load(),
		ElapsedMs: time.Since(started).Milliseconds()})
	return nil
}
