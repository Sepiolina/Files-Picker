//go:build !windows && !darwin

package fsattr

import "io/fs"

func hiddenAttr(fs.FileInfo) bool { return false }
func cloudAttr(fs.FileInfo) bool  { return false }
