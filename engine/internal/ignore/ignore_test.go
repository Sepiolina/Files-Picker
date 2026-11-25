package ignore

import "testing"

func TestMatch(t *testing.T) {
	m := Parse("# c\nnode_modules/\n*.TMP\n/build\ndocs/drafts\n**/cache/**\n!keep.tmp\n")
	cases := []struct {
		rel   string
		isDir bool
		want  bool
	}{
		{"node_modules", true, true},
		{"a/b/node_modules", true, true},
		{"node_modules", false, false}, // trailing / = folders only
		{"x/file.tmp", false, true},
		{"build", true, true},
		{"a/build", true, false}, // anchored
		{"docs/drafts", true, true},
		{"x/docs/drafts", true, false},
		{"a/cache/b.bin", false, true},
		{"keep.tmp", false, true}, // negation unsupported: still matched by *.TMP
		{"readme.md", false, false},
	}
	for _, c := range cases {
		if got := m.Match(c.rel, c.isDir); got != c.want {
			t.Errorf("Match(%q, dir=%v) = %v, want %v", c.rel, c.isDir, got, c.want)
		}
	}
	var nilM *Matcher
	if nilM.Match("x", false) {
		t.Error("nil matcher must match nothing")
	}
}
