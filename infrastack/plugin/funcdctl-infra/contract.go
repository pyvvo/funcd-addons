package main

import (
	"encoding/json"
	"fmt"
	"path"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

const runtimeName = "opentofu1"

var actions = []string{"apply", "destroy", "plan"}

// shown is a module's configuration as `tofu show -json -module` prints it: the parts the push reads.
type shown struct {
	RootModule struct {
		Variables map[string]variable `json:"variables"`
		Outputs   map[string]struct {
			Sensitive bool `json:"sensitive"`
		} `json:"outputs"`
		ModuleCalls map[string]struct {
			Source string `json:"source"`
		} `json:"module_calls"`
	} `json:"root_module"`
}

// variable is one variable of a module. It has a default when its "default" key is there, even as null.
type variable struct {
	Type       any
	HasDefault bool
	Sensitive  bool
}

func (v *variable) UnmarshalJSON(b []byte) error {
	var fields struct {
		Type      any  `json:"type"`
		Sensitive bool `json:"sensitive"`
	}
	if err := json.Unmarshal(b, &fields); err != nil {
		return err
	}
	var keys map[string]json.RawMessage
	if err := json.Unmarshal(b, &keys); err != nil {
		return err
	}
	_, v.HasDefault = keys["default"]
	v.Type, v.Sensitive = fields.Type, fields.Sensitive
	return nil
}

// obj is a schema object that keeps its keys in order, so that the contract file reads as funcd ADR-0355's Contracts
// write it. A value is a string, a bool, an int, a []string or an obj.
type obj []field

type field struct {
	key   string
	value any
}

// A plan run's finding (funcd ADR-0355 Decision 5).
var planSchema = obj{
	{"type", "object"},
	{"additionalProperties", false},
	{"properties", obj{
		{"add", obj{{"type", "integer"}}},
		{"change", obj{{"type", "integer"}}},
		{"destroy", obj{{"type", "integer"}}},
		{"replace", obj{{"type", "integer"}}},
		{"resources", obj{{"type", "array"}, {"items", obj{{"type", "string"}}}}},
	}},
	{"required", []string{"add", "change", "destroy", "replace", "resources"}},
}

// voidSchema is a side that takes nothing, as funcd ADR-0090 has a Function's.
var voidSchema = obj{{"type", "null"}}

// record is a closed record of properties, or the empty schema when it would have no member (funcd ADR-0355
// Decision 3).
func record(properties obj, required []string) obj {
	if len(properties) == 0 {
		return obj{}
	}
	out := obj{{"type", "object"}, {"additionalProperties", false}, {"properties", properties}}
	if len(required) > 0 {
		out = append(out, field{"required", required})
	}
	return out
}

// schema is the schema of a variable's type as `tofu show -json -module` prints it (funcd ADR-0355 Contracts' table).
func schema(tofuType any) obj {
	scalars := map[string]string{"string": "string", "number": "number", "bool": "boolean"}
	switch t := tofuType.(type) {
	case string:
		if s, ok := scalars[t]; ok {
			return obj{{"type", s}}
		}
	case []any:
		if len(t) < 2 {
			break
		}
		kind, _ := t[0].(string)
		switch kind {
		case "list", "set":
			return obj{{"type", "array"}, {"items", schema(t[1])}}
		case "tuple":
			return obj{{"type", "array"}, {"items", obj{}}}
		case "map":
			return obj{{"type", "object"}, {"additionalProperties", schema(t[1])}}
		case "object":
			inner, ok := t[1].(map[string]any)
			if !ok {
				break
			}
			optional := map[string]bool{}
			if len(t) > 2 {
				names, _ := t[2].([]any)
				for _, name := range names {
					if s, ok := name.(string); ok {
						optional[s] = true
					}
				}
			}
			var properties obj
			var required []string
			for _, name := range sortedKeys(inner) {
				properties = append(properties, field{name, schema(inner[name])})
				if !optional[name] {
					required = append(required, name)
				}
			}
			return record(properties, required)
		}
	}
	return obj{}
}

// contract is the module's contract from its configuration (funcd ADR-0355 Decision 3): the run's input of an action
// and the variables that are not sensitive, required when they have no default, and its output of the plain outputs
// or a plan run's finding.
func contract(m shown) obj {
	var variables obj
	var needed []string
	for _, name := range sortedKeys(m.RootModule.Variables) {
		v := m.RootModule.Variables[name]
		if v.Sensitive {
			continue
		}
		variables = append(variables, field{name, schema(v.Type)})
		if !v.HasDefault {
			needed = append(needed, name)
		}
	}
	// A module with no plain variable takes none: its variables are void, and they are required only when a plain
	// variable has no default (funcd ADR-0355 Decision 3, amended 2026-11-24).
	inputs := record(variables, needed)
	if len(inputs) == 0 {
		inputs = voidSchema
	}
	var outputs obj
	for _, name := range sortedKeys(m.RootModule.Outputs) {
		if !m.RootModule.Outputs[name].Sensitive {
			outputs = append(outputs, field{name, obj{}})
		}
	}
	required := []string{"action"}
	if len(needed) > 0 {
		required = append(required, "variables")
	}
	return obj{
		{"runtime", runtimeName},
		{"input", obj{
			{"type", "object"},
			{"additionalProperties", false},
			{"properties", obj{
				{"action", obj{{"type", "string"}, {"enum", actions}}},
				{"variables", inputs},
			}},
			{"required", required},
		}},
		{"output", record(obj{{"outputs", record(outputs, nil)}, {"plan", planSchema}}, nil)},
	}
}

// moduleCalls are the calls of the module at `at`, a folder relative to the root module's, that the push refuses
// (funcd ADR-0355 Decision 2's step 1): a source that is no relative path, which a run's init would fetch, and one
// that leaves the root module's folder, which the bundle would not hold. Each is named as `<call> (<source>)`.
func moduleCalls(m shown, at string) []string {
	var refused []string
	for _, name := range sortedKeys(m.RootModule.ModuleCalls) {
		source := m.RootModule.ModuleCalls[name].Source
		if _, ok := child(at, source); !ok {
			refused = append(refused, fmt.Sprintf("%s (%s)", name, source))
		}
	}
	return refused
}

// child is the folder, relative to the root module's, that a call's source names from the module at `at`; it is not
// ok when the source is no relative path inside the root module's folder.
func child(at, source string) (string, bool) {
	if !strings.HasPrefix(source, "./") && !strings.HasPrefix(source, "../") {
		return "", false
	}
	p := path.Join(at, source)
	if p == ".." || strings.HasPrefix(p, "../") || path.IsAbs(p) {
		return "", false
	}
	return p, true
}

var (
	plainScalar = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_.-]*$`)
	yamlWords   = []string{"true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"}
)

// toYAML is value as block-style YAML: mappings and sequences of scalars, an empty mapping as {}.
func toYAML(value obj, pad string) string {
	var lines []string
	for _, f := range value {
		switch item := f.value.(type) {
		case obj:
			if len(item) > 0 {
				lines = append(lines, pad+scalar(f.key)+":", strings.TrimRight(toYAML(item, pad+"  "), "\n"))
				continue
			}
		case []string:
			if len(item) > 0 {
				lines = append(lines, pad+scalar(f.key)+":")
				for _, v := range item {
					lines = append(lines, pad+"  - "+scalar(v))
				}
				continue
			}
		}
		lines = append(lines, pad+scalar(f.key)+": "+scalar(f.value))
	}
	return strings.Join(lines, "\n") + "\n"
}

func scalar(value any) string {
	switch v := value.(type) {
	case bool:
		return strconv.FormatBool(v)
	case int:
		return strconv.Itoa(v)
	case obj:
		return "{}"
	case []string:
		return "[]"
	case nil:
		return "null"
	case string:
		if plainScalar.MatchString(v) && !slices.Contains(yamlWords, strings.ToLower(v)) {
			return v
		}
		return quoted(v)
	}
	return quoted(fmt.Sprint(value))
}

// quoted is s as a JSON string with every character outside printable ASCII escaped, which YAML reads as a
// double-quoted scalar.
func quoted(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"', '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			switch {
			case r >= 0x20 && r <= 0x7e:
				b.WriteRune(r)
			case r > 0xffff:
				r -= 0x10000
				fmt.Fprintf(&b, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
			default:
				fmt.Fprintf(&b, `\u%04x`, r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}
