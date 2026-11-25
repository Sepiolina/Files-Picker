// picker - the File Picker engine.
//
// Usage:
//
//	picker <command> [--job job.json] [--format json|text] [--stdin-control]
//
// Commands:
//
//	inspect   parse the name list and report entries / unique / duplicates
//	scan      find matching files and report them (nothing is written)
//	export    scan, then write the zip / tar / folder (+ CSV reports)
//	tree      list a folder tree (names, sizes, times) in parallel
//	hash      checksum a list of files in parallel
//	example   print an example job file
//	version   print the engine version
//
// The job is read from --job, or else from the first line of stdin.
// With --stdin-control (used by the GUI) the engine keeps reading stdin after
// the job: a line "cancel", or stdin closing, cancels the run cleanly.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"filepicker/engine/internal/events"
	"filepicker/engine/internal/export"
	"filepicker/engine/internal/job"
	"filepicker/engine/internal/match"
	"filepicker/engine/internal/names"
	"filepicker/engine/internal/report"
	"filepicker/engine/internal/scan"
)

const version = "2.1.0" // 2.1: adds the tree and hash commands

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout))
}

func run(args []string, stdin io.Reader, stdout io.Writer) int {
	if len(args) == 0 || strings.HasPrefix(args[0], "-") {
		fmt.Fprintln(os.Stderr, "usage: picker <inspect|scan|export|tree|hash|example|version> [--job file] [--format json|text] [--stdin-control]")
		return 2
	}
	command := args[0]
	switch command {
	case "version":
		fmt.Fprintln(stdout, version)
		return 0
	case "example":
		b, _ := json.MarshalIndent(job.Default(), "", "  ")
		fmt.Fprintln(stdout, string(b))
		return 0
	case "tree", "hash":
		return runFS(command, args[1:], stdin, stdout)
	case "inspect", "scan", "export":
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n", command)
		return 2
	}

	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	jobPath := flags.String("job", "", "job JSON file (default: first line of stdin)")
	format := flags.String("format", "json", "output format: json | text")
	control := flags.Bool("stdin-control", false, "after the job, read 'cancel' lines from stdin")
	if err := flags.Parse(args[1:]); err != nil {
		return 2
	}
	emit := events.NewEmitter(stdout, *format == "text")

	// Read the job.
	in := bufio.NewReaderSize(stdin, 1<<20)
	var j job.Job
	var err error
	if *jobPath != "" {
		j, err = job.Load(*jobPath)
	} else {
		var line string
		line, err = in.ReadString('\n')
		if err == nil || (err == io.EOF && line != "") {
			j, err = job.Parse([]byte(line))
		}
	}
	if err != nil {
		emit.Emit(events.Error{Type: "error", Message: err.Error()})
		return 1
	}

	// Cancellation: Ctrl+C / SIGTERM, or "cancel" from the GUI.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if *control {
		ctx = watchStdin(ctx, in)
	}

	err = execute(ctx, command, j, emit)
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

// execute runs one command. It is the whole pipeline in one readable place:
//
//	list -> names -> (scan) -> (export) -> (reports)
func execute(ctx context.Context, command string, j job.Job, emit *events.Emitter) error {
	started := time.Now()

	// 1. Names
	entries, err := names.Load(j.List)
	if err != nil {
		return err
	}
	set, err := names.Build(entries, j.Match.CaseSensitive, j.Match.Duplicates)
	emit.Emit(listEvent(set))
	if err != nil {
		return err
	}
	if command == "inspect" {
		emit.Emit(events.Done{Type: "done", Command: command, Warnings: []string{},
			ElapsedMs: time.Since(started).Milliseconds()})
		return nil
	}
	if len(set.Names) == 0 {
		return fmt.Errorf("the name list is empty")
	}
	if j.Source == "" {
		return fmt.Errorf("no folder to search was given")
	}

	// 2. Scan
	opt := scan.Options{
		Root: j.Source, Recursive: j.Recursive, Target: j.Match.Target,
		Extensions: j.Match.ExtensionSet(), Overlap: j.Match.Overlap,
	}
	if command == "export" && j.Output.Path != "" {
		if abs, err := filepath.Abs(j.Output.Path); err == nil {
			opt.Exclude = []string{abs, abs + ".partial"} // never pick up our own output
		}
	}
	var sc scan.Counters
	stopTicker := every(150*time.Millisecond, func() {
		emit.Emit(events.Progress{Type: "progress", Phase: "scan",
			Checked: sc.Checked.Load(), Matched: sc.Matched.Load()})
	})
	res, err := scan.Run(ctx, opt, set, match.New(set, j.Match.Mode, j.Match.CaseSensitive), &sc)
	stopTicker()
	if err != nil {
		return err
	}
	emit.Emit(previewEvent(res, set, j.PreviewLimit))

	var totalBytes int64
	for _, f := range res.Files {
		totalBytes += f.Size
	}
	if command == "scan" {
		emit.Emit(events.Done{Type: "done", Command: command, Exported: len(res.Files), Bytes: totalBytes,
			Warnings: res.Warnings, ElapsedMs: time.Since(started).Milliseconds()})
		return nil
	}

	// 3. Export
	if j.Output.Path == "" {
		return fmt.Errorf("no output location was given")
	}
	plan := export.Plan(res.Files, set, j.Output.GroupByName)
	var planBytes int64
	for _, e := range plan {
		planBytes += e.Size
	}
	var ec export.Counters
	stopTicker = every(150*time.Millisecond, func() {
		emit.Emit(events.Progress{Type: "progress", Phase: "export",
			Bytes: ec.Bytes.Load(), TotalBytes: planBytes,
			Files: ec.Files.Load(), TotalFiles: int64(len(plan))})
	})
	warnings, err := export.Run(ctx, plan, j.Output, &ec)
	stopTicker()
	if err != nil {
		return err
	}

	// 4. Reports
	var reports []string
	if j.Output.Report {
		reports, err = report.Write(reportBase(j.Output), res.Files, set)
		if err != nil {
			warnings = append(warnings, "could not write report: "+err.Error())
		}
	}

	emit.Emit(events.Done{Type: "done", Command: command, Output: j.Output.Path, Reports: reports,
		Exported: len(plan), Bytes: planBytes, Warnings: append(res.Warnings, warnings...),
		ElapsedMs: time.Since(started).Milliseconds()})
	return nil
}

// reportBase: "out.zip" -> "out", "out.tar.gz" -> "out", folder -> "folder/_report".
func reportBase(o job.Output) string {
	if o.Type == "folder" {
		return filepath.Join(o.Path, "_report")
	}
	p := o.Path
	for _, ext := range []string{".tar.gz", ".zip", ".tar"} {
		if strings.HasSuffix(strings.ToLower(p), ext) {
			return p[:len(p)-len(ext)]
		}
	}
	return p
}

func listEvent(set *names.Set) events.List {
	ev := events.List{Type: "list", Entries: set.Entries, Unique: len(set.Names),
		Duplicated: set.Duplicated(), Groups: [][]string{}}
	for _, n := range set.Names {
		if len(n.Spellings) > 1 && len(ev.Groups) < 200 {
			ev.Groups = append(ev.Groups, n.Spellings)
		}
	}
	return ev
}

func previewEvent(res *scan.Result, set *names.Set, limit int) events.Preview {
	text := func(idx []int) []string {
		out := make([]string, len(idx))
		for i, n := range idx {
			out[i] = set.Names[n].Text
		}
		return out
	}
	ev := events.Preview{Type: "preview", Checked: res.Checked, Matched: len(res.Files),
		Files: []events.PreviewFile{}, Names: make([]events.NameCount, len(set.Names)),
		Warnings: res.Warnings}
	if ev.Warnings == nil {
		ev.Warnings = []string{}
	}
	for i, f := range res.Files {
		ev.TotalBytes += f.Size
		if limit <= 0 || i < limit {
			ev.Files = append(ev.Files, events.PreviewFile{Path: f.Rel, Size: f.Size,
				Matched: text(f.Matched), Assigned: text(f.Assigned)})
		}
	}
	ev.Truncated = len(ev.Files) < len(res.Files)
	for i, c := range report.Counts(res.Files, set) {
		n := set.Names[i]
		ev.Names[i] = events.NameCount{Name: n.Text, Count: c}
		if len(n.Spellings) > 1 {
			ev.Names[i].Spellings = n.Spellings
		}
		if c == 0 {
			ev.NotFound++
		}
	}
	return ev
}

// watchStdin cancels the context when the GUI sends "cancel" or goes away.
func watchStdin(parent context.Context, in *bufio.Reader) context.Context {
	ctx, cancel := context.WithCancel(parent)
	go func() {
		for {
			line, err := in.ReadString('\n')
			if strings.TrimSpace(line) == "cancel" || err != nil {
				cancel()
				return
			}
		}
	}()
	return ctx
}

// every calls fn on an interval until the returned stop func is called;
// stop also calls fn one final time so the last numbers are always reported.
func every(d time.Duration, fn func()) (stop func()) {
	done := make(chan struct{})
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		t := time.NewTicker(d)
		defer t.Stop()
		for {
			select {
			case <-t.C:
				fn()
			case <-done:
				return
			}
		}
	}()
	return func() {
		close(done)
		<-finished
		fn()
	}
}
