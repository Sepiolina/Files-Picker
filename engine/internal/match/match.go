// Package match decides which names appear in a file name.
//
// The naive approach (test every name against every file) is
// files × names comparisons: 180k × 180k = 32 billion. Instead we put all
// names in a hash map and slide a window over the file name, one window size
// per distinct name length. A 40-character file name with names of 6 different
// lengths needs only ~240 map lookups, no matter how many names there are.
package match

import (
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"filepicker/engine/internal/names"
)

type Matcher struct {
	mode          string         // contains | whole | exact | prefix | suffix
	caseSensitive bool           //
	index         map[string]int // name key -> position in the names list
	lengths       []int          // distinct key lengths (bytes), ascending
}

func New(set *names.Set, mode string, caseSensitive bool) *Matcher {
	m := &Matcher{mode: mode, caseSensitive: caseSensitive, index: map[string]int{}}
	seen := map[int]bool{}
	for i, n := range set.Names {
		m.index[n.Key] = i
		if !seen[len(n.Key)] {
			seen[len(n.Key)] = true
			m.lengths = append(m.lengths, len(n.Key))
		}
	}
	sort.Ints(m.lengths)
	return m
}

// Find returns the positions of every name found in subject.
func (m *Matcher) Find(subject string) []int {
	s := subject
	if !m.caseSensitive {
		s = strings.ToLower(s)
	}
	var found []int
	add := func(key string) {
		if i, ok := m.index[key]; ok {
			found = append(found, i)
		}
	}

	switch m.mode {
	case "exact":
		add(s)
	case "prefix":
		for _, k := range m.lengths {
			if k > len(s) {
				break
			}
			add(s[:k])
		}
	case "suffix":
		for _, k := range m.lengths {
			if k > len(s) {
				break
			}
			add(s[len(s)-k:])
		}
	default: // contains | whole
		seen := map[int]bool{}
		for _, k := range m.lengths {
			for start := 0; start+k <= len(s); start++ {
				if m.mode == "whole" && !isWholePart(s, start, start+k) {
					continue
				}
				if i, ok := m.index[s[start:start+k]]; ok && !seen[i] {
					seen[i] = true
					found = append(found, i)
				}
			}
		}
	}
	return found
}

// isWholePart reports whether s[start:end] is not glued to a letter or digit
// on either side, so "abc" matches "gg_abc_1" but not "gg_abcde_1".
func isWholePart(s string, start, end int) bool {
	if start > 0 {
		r, _ := utf8.DecodeLastRuneInString(s[:start])
		if isWordChar(r) {
			return false
		}
	}
	if end < len(s) {
		r, _ := utf8.DecodeRuneInString(s[end:])
		if isWordChar(r) {
			return false
		}
	}
	return true
}

func isWordChar(r rune) bool { return unicode.IsLetter(r) || unicode.IsDigit(r) }

// Assign picks which of the found names a file "belongs to" when it matched
// several (e.g. both "ABC" and "ABCDE"). It matters for grouping by name.
//
//	longest  - most specific name wins     shortest - least specific wins
//	first    - earliest in the list wins   all      - keep every match
func Assign(found []int, set *names.Set, rule string) []int {
	if len(found) <= 1 || rule == "all" {
		out := append([]int(nil), found...)
		sort.Ints(out)
		return out
	}
	best := found[0]
	for _, i := range found[1:] {
		a, b := len(set.Names[i].Key), len(set.Names[best].Key)
		switch {
		case rule == "longest" && (a > b || a == b && i < best),
			rule == "shortest" && (a < b || a == b && i < best),
			rule == "first" && i < best:
			best = i
		}
	}
	return []int{best}
}
