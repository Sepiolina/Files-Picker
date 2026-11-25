package names

import (
	"reflect"
	"testing"

	"filepicker/engine/internal/job"
)

func TestSplit(t *testing.T) {
	cases := []struct {
		text string
		list job.List
		want []string
	}{
		{"ABC\r\nAbC\n\n abc \n", job.List{Separator: "newline", Trim: true}, []string{"ABC", "AbC", "abc"}},
		{"a,b\nc", job.List{Separator: "comma", Trim: true}, []string{"a", "b", "c"}},
		{`id,name` + "\n" + `1,ABC` + "\n" + `2,"x, y"`,
			job.List{Separator: "comma", CSVQuotes: true, Header: true, Column: "name", Trim: true},
			[]string{"ABC", "x, y"}},
		{"1;A\n2;B", job.List{Separator: "semicolon", Column: "2", Trim: true}, []string{"A", "B"}},
		{"a  b\tc", job.List{Separator: "whitespace"}, []string{"a", "b", "c"}},
		{"a::b", job.List{Separator: "custom", Custom: "::"}, []string{"a", "b"}},
	}
	for _, c := range cases {
		got, err := Split(c.text, c.list)
		if err != nil || !reflect.DeepEqual(got, c.want) {
			t.Errorf("Split(%q): got %v, %v; want %v", c.text, got, err, c.want)
		}
	}
}

func TestDuplicateRules(t *testing.T) {
	in := []string{"ABC", "AbC", "abc"}
	if s, _ := Build(in, false, "first"); s.Names[0].Text != "ABC" || len(s.Names[0].Spellings) != 3 {
		t.Errorf("first: %+v", s.Names)
	}
	if s, _ := Build(in, false, "last"); s.Names[0].Text != "abc" {
		t.Errorf("last: %+v", s.Names)
	}
	if _, err := Build(in, false, "error"); err == nil {
		t.Error("error rule should fail")
	}
	if s, _ := Build(in, true, "error"); len(s.Names) != 3 {
		t.Error("case-sensitive: no duplicates expected")
	}
}
