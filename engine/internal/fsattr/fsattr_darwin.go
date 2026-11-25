//go:build darwin

package fsattr

import (
	"io/fs"
	"syscall"
)

const sfDataless = 0x40000000 // SF_DATALESS: iCloud / File Provider placeholder

func hiddenAttr(fs.FileInfo) bool { return false }

func cloudAttr(info fs.FileInfo) bool {
	st, ok := info.Sys().(*syscall.Stat_t)
	return ok && st.Flags&sfDataless != 0
}
