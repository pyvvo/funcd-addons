package main

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
)

const (
	providers = ".funcd-providers"
	lockFile  = ".terraform.lock.hcl"
)

var (
	platforms     = []string{"linux/amd64", "linux/arm64"}
	localFiles    = []string{"terraform.tfstate", "terraform.tfstate.backup", "terraform.tfvars", "terraform.tfvars.json"}
	localSuffixes = []string{".auto.tfvars", ".auto.tfvars.json"}
)

// push pushes the module as funcd ADR-0355 Decision 2 says, and prints the index's <ref>@<digest>.
func push(env *pluginkit.Env, in pushInput) error {
	got, err := pushModule(env.Funcdctl, in.Module, in.Ref)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(env.Out, got)
	return err
}

func pushModule(ctl *pluginkit.Funcdctl, module, ref string) (string, error) {
	module = resolve(module)
	name := filepath.Base(module)
	if !holdsTF(module) {
		return "", fmt.Errorf("%s is no folder that holds a .tf file", name)
	}
	local, err := localFilesOf(module)
	if err != nil {
		return "", err
	}
	if len(local) > 0 {
		return "", fmt.Errorf("%s holds %s, which OpenTofu would load over a run's variables or which holds a state: "+
			"remove it, and set each variable through the stack's config", name, strings.Join(local, ", "))
	}
	eng, err := newEngine()
	if err != nil {
		return "", err
	}
	tmp, err := os.MkdirTemp("", "funcdctl-infra-")
	if err != nil {
		return "", err
	}
	defer func() {
		_ = os.RemoveAll(tmp)
	}()
	stage := filepath.Join(tmp, "stage", name)
	if err := stageCopy(module, stage); err != nil {
		return "", err
	}
	tree, err := shownTree(eng, stage)
	if err != nil {
		return "", err
	}
	doc := toYAML(contract(tree["."]), "")
	lock := []string{"providers", "lock"}
	for _, p := range platforms {
		lock = append(lock, "-platform="+strings.ReplaceAll(p, "/", "_"))
	}
	if _, err := eng.tofu(stage, [][]string{{"get"}, lock}, lockFile, filepath.Join(stage, lockFile)); err != nil {
		return "", err
	}
	if err := copyFile(filepath.Join(stage, lockFile), filepath.Join(module, lockFile)); err != nil {
		return "", err
	}
	tf := tfFiles(stage)
	if len(tf) == 0 {
		return "", fmt.Errorf("the copy of %s holds no .tf file to enter at", name)
	}
	first := tf[0]
	for _, platform := range platforms {
		build := filepath.Join(tmp, strings.ReplaceAll(platform, "/", "-"), name)
		if err := copyTree(stage, build, nil); err != nil {
			return "", err
		}
		mirror := []string{"providers", "mirror", "-platform=" + strings.ReplaceAll(platform, "/", "_"), providers}
		if _, err := eng.tofu(stage, [][]string{{"get"}, mirror}, providers, filepath.Join(build, providers)); err != nil {
			return "", err
		}
		if err := os.WriteFile(filepath.Join(build, name+".contract.yaml"), []byte(doc), 0o666); err != nil {
			return "", err
		}
		if _, err := ctl.Run("push", build, tagged(ref, platform), "--entry", first, "--platform", platform); err != nil {
			return "", err
		}
	}
	index := []string{"index", ref}
	for _, p := range platforms {
		index = append(index, tagged(ref, p))
	}
	out, err := ctl.Run(index...)
	if err != nil {
		return "", err
	}
	// funcdctl index prints <ref>@<digest> last.
	lines := strings.FieldsFunc(strings.TrimSpace(out), func(r rune) bool { return r == '\n' || r == '\r' })
	if len(lines) == 0 {
		return "", fmt.Errorf("funcdctl index printed no <ref>@<digest>")
	}
	return lines[len(lines)-1], nil
}

// resolve is path made absolute, with its symbolic links resolved when it is there.
func resolve(path string) string {
	abs, err := filepath.Abs(path)
	if err != nil {
		return path
	}
	if real, err := filepath.EvalSymlinks(abs); err == nil {
		return real
	}
	return abs
}

// holdsTF reports whether module is a folder that holds a .tf file.
func holdsTF(module string) bool {
	info, err := os.Stat(module)
	return err == nil && info.IsDir() && len(tfFiles(module)) > 0
}

// tfFiles are the .tf files of folder, by name.
func tfFiles(folder string) []string {
	entries, _ := os.ReadDir(folder)
	var names []string
	for _, e := range entries {
		info, err := os.Stat(filepath.Join(folder, e.Name()))
		if err == nil && info.Mode().IsRegular() && suffix(e.Name()) == ".tf" {
			names = append(names, e.Name())
		}
	}
	return names
}

// suffix is a file name's last extension; a name that starts with its only dot has none.
func suffix(name string) string {
	i := strings.LastIndex(name, ".")
	if i <= 0 || i == len(name)-1 {
		return ""
	}
	return name[i:]
}

// localFilesOf are the files under the module's folder, outside .terraform/, that OpenTofu would load over a run's
// variables or that hold a state (funcd ADR-0355 Decision 2's step 1): each folder's files, then its folders.
func localFilesOf(module string) ([]string, error) {
	var found []string
	var walk func(folder string) error
	walk = func(folder string) error {
		entries, err := os.ReadDir(folder)
		if err != nil {
			return err
		}
		var folders []string
		for _, e := range entries {
			full := filepath.Join(folder, e.Name())
			info, err := os.Stat(full)
			if err == nil && info.IsDir() {
				// A link to a folder is not followed, so the walk stays inside the module's folder.
				if e.IsDir() && e.Name() != ".terraform" {
					folders = append(folders, full)
				}
				continue
			}
			if slices.Contains(localFiles, e.Name()) || hasSuffix(e.Name(), localSuffixes) {
				rel, err := filepath.Rel(module, full)
				if err != nil {
					return err
				}
				found = append(found, filepath.ToSlash(rel))
			}
		}
		for _, f := range folders {
			if err := walk(f); err != nil {
				return err
			}
		}
		return nil
	}
	return found, walk(module)
}

func hasSuffix(name string, suffixes []string) bool {
	for _, s := range suffixes {
		if strings.HasSuffix(name, s) {
			return true
		}
	}
	return false
}

// stageCopy copies the module's files as a bundle holds them: no .terraform/, and at its top no provider folder and
// no contract file of its own.
func stageCopy(module, into string) error {
	name := filepath.Base(module)
	own := []string{name + ".contract.yaml", name + ".contract.json", "__funcd_contract.json", providers}
	return copyTree(module, into, func(top bool, entry string) bool {
		return entry == ".terraform" || top && slices.Contains(own, entry)
	})
}

// copyTree copies the folder src to dst, links as links, keeping each file's mode and time, but for the entries that
// skip, told whether they are at the top, names.
func copyTree(src, dst string, skip func(top bool, entry string) bool) error {
	var copyDir func(src, dst string, top bool) error
	copyDir = func(src, dst string, top bool) error {
		info, err := os.Stat(src)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(dst, 0o777); err != nil {
			return err
		}
		entries, err := os.ReadDir(src)
		if err != nil {
			return err
		}
		for _, e := range entries {
			if skip != nil && skip(top, e.Name()) {
				continue
			}
			from, to := filepath.Join(src, e.Name()), filepath.Join(dst, e.Name())
			switch {
			case e.Type()&fs.ModeSymlink != 0:
				target, err := os.Readlink(from)
				if err != nil {
					return err
				}
				if err := os.Symlink(target, to); err != nil {
					return err
				}
			case e.IsDir():
				if err := copyDir(from, to, false); err != nil {
					return err
				}
			default:
				if err := copyFile(from, to); err != nil {
					return err
				}
				if err := keepStat(from, to); err != nil {
					return err
				}
			}
		}
		if err := os.Chmod(dst, info.Mode()&(fs.ModePerm|fs.ModeSetuid|fs.ModeSetgid|fs.ModeSticky)); err != nil {
			return err
		}
		return os.Chtimes(dst, info.ModTime(), info.ModTime())
	}
	return copyDir(src, dst, true)
}

// copyFile writes src's content to dst; a dst that is there keeps its mode.
func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0o666)
}

// keepStat gives dst the mode and the time of src.
func keepStat(src, dst string) error {
	info, err := os.Stat(src)
	if err != nil {
		return err
	}
	if err := os.Chmod(dst, info.Mode()&(fs.ModePerm|fs.ModeSetuid|fs.ModeSetgid|fs.ModeSticky)); err != nil {
		return err
	}
	return os.Chtimes(dst, info.ModTime(), info.ModTime())
}

// shownTree is the configuration of the module and of each local child it calls, read with `tofu show -json
// -module`, by folder relative to the module's. A refused call ends the push, naming it (funcd ADR-0355 Decision 2's
// step 1).
func shownTree(eng *engine, stage string) (map[string]shown, error) {
	tree := map[string]shown{}
	level := []string{"."}
	for len(level) > 0 {
		var commands [][]string
		for _, at := range level {
			commands = append(commands, []string{"show", "-json", "-no-color", "-module=" + at})
		}
		outs, err := eng.tofu(stage, commands, "", "")
		if err != nil {
			return nil, err
		}
		if len(outs) != len(level) {
			return nil, fmt.Errorf("OpenTofu printed %d configurations for %d modules", len(outs), len(level))
		}
		var found []string
		for i, at := range level {
			var m shown
			if err := json.Unmarshal([]byte(outs[i]), &m); err != nil {
				return nil, fmt.Errorf("read the configuration of %s: %w", at, err)
			}
			tree[at] = m
			if refused := moduleCalls(m, at); len(refused) > 0 {
				where := "the module"
				if at != "." {
					where = "its child module " + at
				}
				return nil, fmt.Errorf("%s calls %s, which the bundle would not hold: a call's source must be a relative "+
					"path inside the module's folder", where, strings.Join(refused, ", "))
			}
			for _, name := range sortedKeys(m.RootModule.ModuleCalls) {
				p, _ := child(at, m.RootModule.ModuleCalls[name].Source)
				if _, seen := tree[p]; !seen && !slices.Contains(found, p) {
					found = append(found, p)
				}
			}
		}
		level = found
	}
	return tree, nil
}

// tagged is the ref of the bundle of platform: <ref>-linux-<arch>.
func tagged(ref, platform string) string {
	return ref + "-" + strings.ReplaceAll(platform, "/", "-")
}
