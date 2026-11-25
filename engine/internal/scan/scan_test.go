package scan

import (
	"sort"
	"strings"
	"testing"
)

func TestNaturalLess(t *testing.T) {
	in := []string{"part_10.bin", "part_2.bin", "Part_1.bin", "a/b/x.txt", "a/x.txt", "part_02b.bin"}
	sort.Slice(in, func(i, j int) bool { return NaturalLess(in[i], in[j]) })
	want := "a/b/x.txt,a/x.txt,Part_1.bin,part_2.bin,part_02b.bin,part_10.bin"
	if got := strings.Join(in, ","); got != want {
		t.Errorf("got  %s\nwant %s", got, want)
	}
}
