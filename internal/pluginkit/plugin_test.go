package pluginkit_test

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"testing"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
	"github.com/pyvvo/funcd-addons/internal/pluginkit/plugintest"
	"github.com/spf13/pflag"
)

func TestMain(m *testing.M) {
	plugintest.Main(m, map[string]plugintest.Fake{"funcdctl": fakeFuncdctl})
}

// fakeFuncdctl prints its arguments for get, refuses fail with a message and silent without one, prints what apply
// reads, and asks [y/N] for ask, exiting 4 on any answer but y.
func fakeFuncdctl(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	switch args[0] {
	case "get":
		fmt.Fprintf(stdout, `{"args": %q}`+"\n", strings.Join(args, " "))
	case "fail":
		fmt.Fprintln(stdout, "half a table")
		fmt.Fprintln(stderr, "funcdctl: refused")
		return 1
	case "silent":
		return 3
	case "apply":
		manifest, _ := io.ReadAll(stdin)
		fmt.Fprintf(stdout, "applied %s\n", manifest)
	case "ask":
		var answer string
		fmt.Fprint(stdout, "approve? [y/N] ")
		_, _ = fmt.Fscanln(stdin, &answer)
		if answer != "y" {
			fmt.Fprintln(stderr, "funcdctl: not approved")
			return 4
		}
		fmt.Fprintln(stdout, "approved")
	}
	return 0
}

// echoInput is the input of the test plugin's one command.
type echoInput struct {
	Name      string
	Namespace string
	Group     string
	Output    pluginkit.Format
}

func (in echoInput) Validate() error {
	if in.Name == "bad" {
		return errors.New("bad is no name")
	}
	return nil
}

// testPlugin is a plugin whose command echo runs service.
func testPlugin(service func(env *pluginkit.Env, in echoInput) error) *pluginkit.Plugin {
	return &pluginkit.Plugin{
		Name:  "test",
		Short: "A test plugin",
		Commands: []pluginkit.Command{pluginkit.Spec[echoInput]{
			Use:   "echo <name>",
			Short: "Print the input",
			Flags: func(fs *pflag.FlagSet, in *echoInput) {
				pluginkit.Namespace(fs, &in.Namespace)
				pluginkit.Output(fs, &in.Output)
				fs.StringVar(&in.Group, "group", "", "the group")
			},
			Required: []string{"group"},
			Args: func(in *echoInput, args []string) {
				in.Name = args[0]
			},
			Run: service,
		}},
	}
}

func printInput(env *pluginkit.Env, in echoInput) error {
	_, err := fmt.Fprintf(env.Out, "%+v\n", in)
	return err
}

func run(p *pluginkit.Plugin, stdin string, args ...string) (int, string, string) {
	var stdout, stderr bytes.Buffer
	code := p.Run(args, strings.NewReader(stdin), &stdout, &stderr)
	return code, stdout.String(), stderr.String()
}

func TestASpecBindsItsFlagsArgumentsAndDefaults(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		args []string
		want string
	}{
		{[]string{"echo", "login", "--group", "g"}, "{Name:login Namespace:default Group:g Output:}\n"},
		{[]string{"echo", "-n", "team", "login", "-o", "json", "--group", "g"}, "{Name:login Namespace:team Group:g Output:json}\n"},
	} {
		code, out, stderr := run(testPlugin(printInput), "", c.args...)
		if code != 0 || out != c.want || stderr != "" {
			t.Errorf("%v: exit %d, stdout %q, stderr %q", c.args, code, out, stderr)
		}
	}
}

// Every error ends the plugin with `funcdctl <plugin>: <message>` on stderr and exit 1: a required flag, the number
// of arguments, the input's Validate, a flag's value, an unknown command or flag, and the service.
func TestEveryErrorIsTheWrappersMessageAndExit1(t *testing.T) {
	t.Parallel()
	failing := testPlugin(func(*pluginkit.Env, echoInput) error { return errors.New("the service failed") })
	for _, c := range []struct {
		p    *pluginkit.Plugin
		args []string
		want string
	}{
		{testPlugin(printInput), []string{"echo", "login"}, "--group is required"},
		{testPlugin(printInput), []string{"echo", "--group", "g"}, "usage: funcdctl test echo <name> [flags]"},
		{testPlugin(printInput), []string{"echo", "a", "b", "--group", "g"}, "usage: funcdctl test echo <name> [flags]"},
		{testPlugin(printInput), []string{"echo", "bad", "--group", "g"}, "bad is no name"},
		{testPlugin(printInput), []string{"echo", "login", "--group", "g", "-o", "yaml"},
			`invalid argument "yaml" for "-o, --output" flag: "yaml" is no output format: give json`},
		{testPlugin(printInput), []string{"nope"}, `unknown command "nope" for "funcdctl test"`},
		{testPlugin(printInput), []string{"echo", "login", "--group", "g", "--bogus"}, "unknown flag: --bogus"},
		{failing, []string{"echo", "login", "--group", "g"}, "the service failed"},
	} {
		code, out, stderr := run(c.p, "", c.args...)
		if code != 1 || out != "" || stderr != "funcdctl test: "+c.want+"\n" {
			t.Errorf("%v: exit %d, stdout %q, stderr %q", c.args, code, out, stderr)
		}
	}
}

func TestAnExitCodeEndsThePluginWithItAndNoMessage(t *testing.T) {
	t.Parallel()
	p := testPlugin(func(*pluginkit.Env, echoInput) error { return fmt.Errorf("ran: %w", pluginkit.ExitCode(5)) })
	if code, out, stderr := run(p, "", "echo", "login", "--group", "g"); code != 5 || out != "" || stderr != "" {
		t.Fatalf("exit %d, stdout %q, stderr %q", code, out, stderr)
	}
}

// A plugin named with no command prints its help where errors go and fails; --help prints it where output goes.
func TestHelp(t *testing.T) {
	t.Parallel()
	code, out, stderr := run(testPlugin(printInput), "")
	if code != 1 || out != "" || !strings.Contains(stderr, "funcdctl test [command]") || !strings.Contains(stderr, "echo") {
		t.Errorf("no command: exit %d, stdout %q, stderr %q", code, out, stderr)
	}
	code, out, stderr = run(testPlugin(printInput), "", "echo", "--help")
	if code != 0 || stderr != "" || !strings.Contains(out, "funcdctl test echo <name> [flags]") ||
		!strings.Contains(out, "the group (required)") || strings.Contains(out, "--help") {
		t.Errorf("--help: exit %d, stdout %q, stderr %q", code, out, stderr)
	}
}

// fakeOnPath puts the fake funcdctl on PATH and unsets FUNCDCTL, or sets it to the fake's path with viaEnv.
func fakeOnPath(t *testing.T, viaEnv bool) {
	t.Helper()
	bin := plugintest.Install(t, "funcdctl")
	t.Setenv("FUNCDCTL", "")
	if viaEnv {
		t.Setenv("PATH", t.TempDir())
		t.Setenv("FUNCDCTL", filepath.Join(bin, "funcdctl"))
	}
}

// callPlugin is a plugin whose echo command runs call with the plugin's funcdctl and prints what it returned.
func callPlugin(call func(f *pluginkit.Funcdctl) (string, error)) *pluginkit.Plugin {
	return testPlugin(func(env *pluginkit.Env, in echoInput) error {
		out, err := call(env.Funcdctl)
		if err != nil {
			return err
		}
		_, err = fmt.Fprint(env.Out, out)
		return err
	})
}

func TestFuncdctlIsFUNCDCTLElseTheOneOnPATH(t *testing.T) {
	for _, viaEnv := range []bool{true, false} {
		fakeOnPath(t, viaEnv)
		p := callPlugin(func(f *pluginkit.Funcdctl) (string, error) { return f.Run("get", "x") })
		if code, out, stderr := run(p, "", "echo", "login", "--group", "g"); code != 0 || out != `{"args": "get x"}`+"\n" {
			t.Errorf("FUNCDCTL set %v: exit %d, stdout %q, stderr %q", viaEnv, code, out, stderr)
		}
	}
}

func TestRunReturnsFuncdctlsOutputAndItsOwnMessage(t *testing.T) {
	fakeOnPath(t, true)
	for _, c := range []struct {
		args []string
		want string
	}{
		{[]string{"fail"}, "funcdctl test: funcdctl: refused\n"},
		{[]string{"silent"}, "funcdctl test: funcdctl silent failed\n"},
	} {
		p := callPlugin(func(f *pluginkit.Funcdctl) (string, error) { return f.Run(c.args...) })
		if code, _, stderr := run(p, "", "echo", "login", "--group", "g"); code != 1 || stderr != c.want {
			t.Errorf("%v: exit %d, stderr %q", c.args, code, stderr)
		}
	}
}

func TestGetDecodesTheObject(t *testing.T) {
	fakeOnPath(t, true)
	p := callPlugin(func(f *pluginkit.Funcdctl) (string, error) {
		var got struct {
			Args string `json:"args"`
		}
		err := f.Get(&got, "job", "edge", "team")
		return got.Args + "\n", err
	})
	if code, out, stderr := run(p, "", "echo", "login", "--group", "g"); code != 0 || out != "get job edge -n team -o json\n" {
		t.Fatalf("exit %d, stdout %q, stderr %q", code, out, stderr)
	}
	bad := callPlugin(func(f *pluginkit.Funcdctl) (string, error) {
		var got []string
		return "", f.JSON(&got, "get", "x")
	})
	if code, _, stderr := run(bad, "", "echo", "login", "--group", "g"); code != 1 || !strings.HasPrefix(stderr, "funcdctl test: read what funcdctl get printed: ") {
		t.Fatalf("exit %d, stderr %q", code, stderr)
	}
}

// Apply hands funcdctl the manifest, and what funcdctl prints goes to the plugin's stderr.
func TestApplyPutsFuncdctlsLinesOnStderr(t *testing.T) {
	fakeOnPath(t, true)
	p := callPlugin(func(f *pluginkit.Funcdctl) (string, error) { return "", f.Apply([]byte("kind: Secret")) })
	if code, out, stderr := run(p, "", "echo", "login", "--group", "g"); code != 0 || out != "" || stderr != "applied kind: Secret\n" {
		t.Fatalf("exit %d, stdout %q, stderr %q", code, out, stderr)
	}
}

// Interactive hands funcdctl the plugin's terminal, and the plugin ends with funcdctl's exit code and none of its own
// words.
func TestInteractiveHandsFuncdctlTheTerminal(t *testing.T) {
	fakeOnPath(t, true)
	p := callPlugin(func(f *pluginkit.Funcdctl) (string, error) { return "", f.Interactive("ask") })
	if code, out, stderr := run(p, "y\n", "echo", "login", "--group", "g"); code != 0 || out != "approve? [y/N] approved\n" || stderr != "" {
		t.Errorf("y: exit %d, stdout %q, stderr %q", code, out, stderr)
	}
	if code, out, stderr := run(p, "n\n", "echo", "login", "--group", "g"); code != 4 || out != "approve? [y/N] " || stderr != "funcdctl: not approved\n" {
		t.Errorf("n: exit %d, stdout %q, stderr %q", code, out, stderr)
	}
}

func TestTablePadsEachColumnButTheLast(t *testing.T) {
	t.Parallel()
	var b bytes.Buffer
	if err := pluginkit.Table(&b, [][]string{{"NAME", "VALUE"}, {"host", "a.b-cdn.net"}, {"FRP_TOKEN", "(sensitive)"}}); err != nil {
		t.Fatal(err)
	}
	if want := "NAME       VALUE\nhost       a.b-cdn.net\nFRP_TOKEN  (sensitive)\n"; b.String() != want {
		t.Errorf("got %q, want %q", b.String(), want)
	}
}

func TestJSONIsIndentedAndKeepsItsCharacters(t *testing.T) {
	t.Parallel()
	var b bytes.Buffer
	if err := pluginkit.JSON(&b, map[string]any{"outputs": map[string]string{"q": "a<b&c"}, "sensitive": []string{}}); err != nil {
		t.Fatal(err)
	}
	if want := "{\n  \"outputs\": {\n    \"q\": \"a<b&c\"\n  },\n  \"sensitive\": []\n}\n"; b.String() != want {
		t.Errorf("got %q, want %q", b.String(), want)
	}
}

func TestARequiredFlagThatIsNotDeclaredIsTheAuthorsMistake(t *testing.T) {
	t.Parallel()
	defer func() {
		if recover() == nil {
			t.Error("no panic")
		}
	}()
	p := &pluginkit.Plugin{Name: "test", Commands: []pluginkit.Command{pluginkit.Spec[echoInput]{
		Use: "echo", Required: []string{"missing"}, Run: printInput,
	}}}
	p.Run([]string{"echo"}, strings.NewReader(""), io.Discard, io.Discard)
}
