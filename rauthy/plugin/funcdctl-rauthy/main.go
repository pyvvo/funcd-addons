// Command funcdctl-rauthy is the funcdctl plugin of the rauthy add-on (funcd ADR-0319): `funcdctl rauthy keys`
// applies the Secret an IdentityProvider binds, holding rauthy's cluster and encryption keys and its first admin, and
// `funcdctl rauthy urls` prints an IdentityProvider's issuer, account and admin URLs.
package main

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/big"
	"os"
	"os/exec"
	"strings"

	"golang.org/x/crypto/argon2"
)

// rauthy reads these from the Addon's secretKeys (rauthy/addon.yaml).
const (
	keyRaft          = "HQL_SECRET_RAFT"
	keyAPI           = "HQL_SECRET_API"
	keyEncKeys       = "ENC_KEYS"
	keyEncActive     = "ENC_KEY_ACTIVE"
	keyAdminEmail    = "BOOTSTRAP_ADMIN_EMAIL"
	keyAdminPassword = "BOOTSTRAP_ADMIN_PASSWORD_ARGON2ID"
)

// The argon2id cost of the first admin's password hash, inside rauthy's own defaults.
const (
	argonMemoryKiB = 64 * 1024
	argonTime      = 3
	argonThreads   = 4
	argonKeyLen    = 32
)

const alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

func main() {
	if len(os.Args) < 2 {
		usage(os.Stderr)
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "keys":
		err = keys(os.Args[2:], os.Stdout)
	case "urls":
		err = urls(os.Args[2:], os.Stdout)
	case "-h", "--help", "help":
		usage(os.Stdout)
		return
	default:
		fmt.Fprintf(os.Stderr, "funcdctl rauthy: unknown command %q\n", os.Args[1])
		usage(os.Stderr)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "funcdctl rauthy:", err)
		os.Exit(1)
	}
}

func usage(w io.Writer) {
	fmt.Fprint(w, `funcdctl rauthy: the rauthy add-on's commands

  funcdctl rauthy keys --admin-email <email> [--name rauthy-keys] [-n default] [--resource-group default] [--print]
      Apply a Secret with new cluster and encryption keys and a first admin, and print the admin's password once.
  funcdctl rauthy urls <identityprovider> [-n default]
      Print the IdentityProvider's issuer, account and admin URLs.
`)
}

func keys(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("keys", flag.ContinueOnError)
	name := fs.String("name", "rauthy-keys", "the Secret's name")
	ns := fs.String("n", "default", "the namespace")
	group := fs.String("resource-group", "default", "the Secret's resource group")
	email := fs.String("admin-email", "", "the first admin's email")
	printOnly := fs.Bool("print", false, "print the Secret instead of applying it")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if !strings.Contains(*email, "@") {
		return errors.New("--admin-email is required")
	}
	password, err := random(24)
	if err != nil {
		return err
	}
	hash, err := argon2id(password)
	if err != nil {
		return err
	}
	data := map[string]string{
		keyAdminEmail:    *email,
		keyAdminPassword: hash,
	}
	for _, k := range []string{keyRaft, keyAPI} {
		if data[k], err = random(48); err != nil {
			return err
		}
	}
	kid, err := random(8)
	if err != nil {
		return err
	}
	enc := make([]byte, 32)
	if _, err := rand.Read(enc); err != nil {
		return err
	}
	data[keyEncKeys] = kid + "/" + base64.StdEncoding.EncodeToString(enc)
	data[keyEncActive] = kid
	manifest := secretManifest(*name, *ns, *group, data)
	if *printOnly {
		_, err := out.Write(manifest)
		return err
	}
	cmd := exec.Command(funcdctl(), "apply", "-f", "-")
	cmd.Stdin, cmd.Stdout, cmd.Stderr = bytes.NewReader(manifest), os.Stderr, os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("apply the Secret %s/%s: %w", *ns, *name, err)
	}
	_, err = fmt.Fprintf(out, "admin: %s\npassword: %s\nStore the password now: funcd keeps only its hash.\n", *email, password)
	return err
}

func urls(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("urls", flag.ContinueOnError)
	ns := fs.String("n", "default", "the namespace")
	// The name may come before the flags, as funcdctl's own commands take it: urls login -n default.
	var name string
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		name, args = args[0], args[1:]
	}
	if err := fs.Parse(args); err != nil {
		return err
	}
	if name == "" && fs.NArg() == 1 {
		name = fs.Arg(0)
	} else if name == "" || fs.NArg() != 0 {
		return errors.New("name one IdentityProvider")
	}
	raw, err := exec.Command(funcdctl(), "get", "identityprovider", name, "-n", *ns, "-o", "json").Output()
	if err != nil {
		return fmt.Errorf("get the IdentityProvider %s/%s: %w", *ns, name, err)
	}
	var idp struct {
		Spec struct {
			Config struct {
				PublicHost string `json:"publicHost"`
				HTTPS      bool   `json:"https"`
			} `json:"config"`
		} `json:"spec"`
	}
	if err := json.Unmarshal(raw, &idp); err != nil {
		return fmt.Errorf("read the IdentityProvider: %w", err)
	}
	scheme := "http"
	if idp.Spec.Config.HTTPS {
		scheme = "https"
	}
	base := scheme + "://" + idp.Spec.Config.PublicHost + "/auth/v1"
	_, err = fmt.Fprintf(out, "issuer:  %s/\naccount: %s/account\nadmin:   %s/admin\n", base, base, base)
	return err
}

// funcdctl is the funcdctl that runs this plugin (FUNCDCTL), else the one on PATH.
func funcdctl() string {
	if p := os.Getenv("FUNCDCTL"); p != "" {
		return p
	}
	return "funcdctl"
}

// secretManifest is a Secret of data, in block-style YAML, each value base64 as a Secret's data holds it.
func secretManifest(name, ns, group string, data map[string]string) []byte {
	var b bytes.Buffer
	fmt.Fprintf(&b, "apiVersion: funcd.io/v1alpha1\nkind: Secret\nmetadata:\n  name: %s\n  namespace: %s\n  resourceGroup: %s\nspec:\n  data:\n",
		name, ns, group)
	for _, k := range []string{keyRaft, keyAPI, keyEncKeys, keyEncActive, keyAdminEmail, keyAdminPassword} {
		fmt.Fprintf(&b, "    %s: %s\n", k, base64.StdEncoding.EncodeToString([]byte(data[k])))
	}
	return b.Bytes()
}

// argon2id is password's PHC string, as rauthy reads BOOTSTRAP_ADMIN_PASSWORD_ARGON2ID.
func argon2id(password string) (string, error) {
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key := argon2.IDKey([]byte(password), salt, argonTime, argonMemoryKiB, argonThreads, argonKeyLen)
	enc := base64.RawStdEncoding
	return fmt.Sprintf("$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s", argon2.Version, argonMemoryKiB, argonTime, argonThreads,
		enc.EncodeToString(salt), enc.EncodeToString(key)), nil
}

// random is n characters of [a-zA-Z0-9] from crypto/rand.
func random(n int) (string, error) {
	out := make([]byte, n)
	size := big.NewInt(int64(len(alnum)))
	for i := range out {
		v, err := rand.Int(rand.Reader, size)
		if err != nil {
			return "", err
		}
		out[i] = alnum[v.Int64()]
	}
	return string(out), nil
}
