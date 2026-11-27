//go:build !windows

package hashing

import (
	"context"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestRunReturnsAfterCancelEvenIfWorkersAreStuck(t *testing.T) {
	old := CancelGrace
	CancelGrace = 100 * time.Millisecond
	defer func() { CancelGrace = old }()

	dir := t.TempDir()
	fifo := filepath.Join(dir, "stuck")
	if err := syscall.Mkfifo(fifo, 0o600); err != nil { // opening a FIFO with no writer blocks forever
		t.Skip("no fifo support")
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- Run(ctx, Options{Root: dir, Files: []string{"stuck"}, Workers: 1}, &Counters{}, func(Result) {})
	}()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("expected a cancellation error")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("Run hung after cancel")
	}
}
