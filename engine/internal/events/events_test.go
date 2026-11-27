package events

import (
	"bytes"
	"strings"
	"testing"
)

type batchEv struct {
	Type string `json:"type"`
}

func (batchEv) Batchable() {}

func TestBatchableEventsAreBufferedUntilANormalEventFlushes(t *testing.T) {
	var out bytes.Buffer
	e := NewEmitter(&out, false)
	e.Emit(batchEv{"dir"})
	e.Emit(batchEv{"dir"})
	if out.Len() != 0 {
		t.Fatalf("batchable events should be buffered, got %q", out.String())
	}
	e.Emit(Cancelled{Type: "cancelled"})
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 3 || !strings.Contains(lines[2], "cancelled") {
		t.Fatalf("order/flush wrong: %q", out.String())
	}
}
