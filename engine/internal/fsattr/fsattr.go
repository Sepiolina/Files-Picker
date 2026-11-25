// Package fsattr answers two per-file questions that differ by operating system:
// is it hidden, and is it a cloud placeholder (a OneDrive / iCloud / Dropbox
// "online-only" file whose content is not on this computer)?
//
// Reading a placeholder's content would make the sync client download it, so scans
// can skip them and never hash them.
package fsattr

import (
	"io/fs"
	"strings"
)

// Hidden: dot-files everywhere, plus the hidden attribute on Windows.
func Hidden(name string, info fs.FileInfo) bool {
	return strings.HasPrefix(name, ".") || hiddenAttr(info)
}

// CloudOnly: the file's data is not stored locally.
func CloudOnly(name string, info fs.FileInfo) bool {
	return cloudAttr(info) || (strings.HasPrefix(name, ".") && strings.HasSuffix(name, ".icloud"))
}
