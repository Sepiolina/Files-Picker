// Package ignore implements the "ignore rules" used by every scan - a small
// subset of .gitignore syntax, case-insensitive, one pattern per line:
//
//	# comment
//	node_modules/     a folder with this name, anywhere        (trailing / = folders only)
//	*.tmp             files/folders whose name matches          (* ? wildcards)
//	/build            only at the top of the scanned folder     (leading / = anchored)
//	docs/drafts       a path (any pattern containing / is anchored to the scanned folder)
//	**/cache/**       ** matches any number of folders
//
// Negation ("!pattern") is not supported and such lines are ignored.
// The Python side (gui/picker_gui/fscore.py) implements the same rules.
package ignore

import (
	"regexp"
	"strings"
)

type rule struct {
	re      *regexp.Regexp
	dirOnly bool
	anchor  bool
}

// Matcher decides whether a path should be skipped. The zero value (and nil) skips nothing.
type Matcher struct{ rules []rule }

// Parse builds a Matcher from the text of a rules file.
func Parse(text string) *Matcher {
	m := &Matcher{}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, "!") {
			continue
		}
		line = strings.ReplaceAll(line, "\\", "/")
		r := rule{}
		if strings.HasSuffix(line, "/") {
			r.dirOnly = true
			line = strings.TrimRight(line, "/")
		}
		if strings.HasPrefix(line, "/") {
			r.anchor = true
			line = strings.TrimLeft(line, "/")
		} else if strings.Contains(line, "/") {
			r.anchor = true
		}
		if line == "" {
			continue
		}
		re, err := regexp.Compile("(?i)^" + translate(line) + "$")
		if err != nil {
			continue
		}
		r.re = re
		m.rules = append(m.rules, r)
	}
	return m
}

// Match reports whether rel (forward slashes, relative to the scanned folder) is ignored.
func (m *Matcher) Match(rel string, isDir bool) bool {
	if m == nil || len(m.rules) == 0 {
		return false
	}
	name := rel
	if i := strings.LastIndexByte(rel, '/'); i >= 0 {
		name = rel[i+1:]
	}
	for _, r := range m.rules {
		if r.dirOnly && !isDir {
			continue
		}
		target := name
		if r.anchor {
			target = rel
		}
		if r.re.MatchString(target) {
			return true
		}
	}
	return false
}

func (m *Matcher) Empty() bool { return m == nil || len(m.rules) == 0 }

func translate(p string) string {
	var b strings.Builder
	rs := []rune(p)
	for i := 0; i < len(rs); {
		switch {
		case rs[i] == '*' && i+2 < len(rs) && rs[i+1] == '*' && rs[i+2] == '/':
			b.WriteString("(?:.*/)?")
			i += 3
		case rs[i] == '*' && i+1 < len(rs) && rs[i+1] == '*':
			b.WriteString(".*")
			i += 2
		case rs[i] == '*':
			b.WriteString("[^/]*")
			i++
		case rs[i] == '?':
			b.WriteString("[^/]")
			i++
		default:
			b.WriteString(regexp.QuoteMeta(string(rs[i])))
			i++
		}
	}
	return b.String()
}
