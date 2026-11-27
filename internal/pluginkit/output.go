package pluginkit

import (
	"encoding/json"
	"io"
	"strings"
	"unicode/utf8"
)

// tablePad is the space between two columns of a table.
const tablePad = 2

// Table writes rows, its header first, as columns: each but the last padded to its widest cell and tablePad spaces.
func Table(w io.Writer, rows [][]string) error {
	var widths []int
	for _, row := range rows {
		for i, cell := range row[:max(len(row)-1, 0)] {
			if i == len(widths) {
				widths = append(widths, 0)
			}
			widths[i] = max(widths[i], utf8.RuneCountInString(cell))
		}
	}
	var b strings.Builder
	for _, row := range rows {
		for i, cell := range row {
			b.WriteString(cell)
			if i < len(row)-1 {
				b.WriteString(strings.Repeat(" ", widths[i]+tablePad-utf8.RuneCountInString(cell)))
			}
		}
		b.WriteByte('\n')
	}
	_, err := io.WriteString(w, b.String())
	return err
}

// JSON writes v as JSON indented by two spaces, with <, > and & as they are.
func JSON(w io.Writer, v any) error {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}
