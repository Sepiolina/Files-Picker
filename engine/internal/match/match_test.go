package match

import (
	"reflect"
	"testing"

	"filepicker/engine/internal/names"
)

func setOf(t *testing.T, caseSensitive bool, list ...string) *names.Set {
	t.Helper()
	s, err := names.Build(list, caseSensitive, "first")
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func found(m *Matcher, s *names.Set, subject string) []string {
	var out []string
	for _, i := range m.Find(subject) {
		out = append(out, s.Names[i].Text)
	}
	return out
}

func TestUserExamples(t *testing.T) {
	s := setOf(t, false, "abc")
	whole := New(s, "whole", false)
	contains := New(s, "contains", false)

	cases := []struct {
		name          string
		whole, substr bool
	}{
		{"gg_abc_", true, true},
		{"dq122130219_abc_1291291", true, true},
		{"gg_abcde_", false, true},
		{"xyzabc1", false, true},
		{"ABC", true, true},
		{"report-abc (2)", true, true},
	}
	for _, c := range cases {
		if got := len(whole.Find(c.name)) > 0; got != c.whole {
			t.Errorf("whole %q: got %v want %v", c.name, got, c.whole)
		}
		if got := len(contains.Find(c.name)) > 0; got != c.substr {
			t.Errorf("contains %q: got %v want %v", c.name, got, c.substr)
		}
	}
}

func TestModesAndCase(t *testing.T) {
	cs := setOf(t, true, "ABC", "AbC", "abc", "ABCDE")
	if got := found(New(cs, "contains", true), cs, "xAbCx"); !reflect.DeepEqual(got, []string{"AbC"}) {
		t.Errorf("case-sensitive contains: %v", got)
	}
	ci := setOf(t, false, "ABC", "AbC", "abc", "ABCDE")
	if len(ci.Names) != 2 || ci.Duplicated() != 2 {
		t.Fatalf("dedupe: %+v", ci)
	}
	if got := found(New(ci, "contains", false), ci, "zz_abcde"); len(got) != 2 {
		t.Errorf("both ABC and ABCDE expected, got %v", got)
	}
	if got := found(New(ci, "exact", false), ci, "abcde"); !reflect.DeepEqual(got, []string{"ABCDE"}) {
		t.Errorf("exact: %v", got)
	}
	if got := found(New(ci, "prefix", false), ci, "abc_x"); !reflect.DeepEqual(got, []string{"ABC"}) {
		t.Errorf("prefix: %v", got)
	}
	if got := found(New(ci, "suffix", false), ci, "x_abcde"); !reflect.DeepEqual(got, []string{"ABCDE"}) {
		t.Errorf("suffix: %v", got)
	}
}

func TestAssign(t *testing.T) {
	s := setOf(t, false, "ABCDE", "ABC", "B")
	all := []int{0, 1, 2}
	want := map[string][]int{"longest": {0}, "shortest": {2}, "first": {0}, "all": {0, 1, 2}}
	for rule, w := range want {
		if got := Assign(all, s, rule); !reflect.DeepEqual(got, w) {
			t.Errorf("%s: got %v want %v", rule, got, w)
		}
	}
}
