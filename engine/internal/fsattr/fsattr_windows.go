//go:build windows

package fsattr

import (
	"io/fs"
	"syscall"
)

const (
	attrHidden         = 0x2
	attrOffline        = 0x1000   // FILE_ATTRIBUTE_OFFLINE
	attrRecallOnOpen   = 0x40000  // FILE_ATTRIBUTE_RECALL_ON_OPEN
	attrRecallOnAccess = 0x400000 // FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS (OneDrive "online-only")
	attrCloudMask      = attrOffline | attrRecallOnOpen | attrRecallOnAccess
)

func attrs(info fs.FileInfo) uint32 {
	if d, ok := info.Sys().(*syscall.Win32FileAttributeData); ok {
		return d.FileAttributes
	}
	return 0
}

func hiddenAttr(info fs.FileInfo) bool { return attrs(info)&attrHidden != 0 }
func cloudAttr(info fs.FileInfo) bool  { return attrs(info)&attrCloudMask != 0 }
