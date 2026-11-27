package main

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/big"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
	"golang.org/x/crypto/argon2"
)

// rauthy reads these from the Addon's secretKeys (rauthy/app/addon.yaml).
const (
	keyRaft          = "HQL_SECRET_RAFT"
	keyAPI           = "HQL_SECRET_API"
	keyEncKeys       = "ENC_KEYS"
	keyEncActive     = "ENC_KEY_ACTIVE"
	keyAdminEmail    = "BOOTSTRAP_ADMIN_EMAIL"
	keyAdminPassword = "BOOTSTRAP_ADMIN_PASSWORD_ARGON2ID"
	// The API key rauthy creates at its first start, which the settings Function uses (funcd ADR-0341).
	keyAPIKey       = "BOOTSTRAP_API_KEY"
	keyAPIKeySecret = "BOOTSTRAP_API_KEY_SECRET"
	settingsKeyName = "settings"
)

// The argon2id cost of the first admin's password hash, inside rauthy's own defaults.
const (
	argonMemoryKiB = 64 * 1024
	argonTime      = 3
	argonThreads   = 4
	argonKeyLen    = 32
)

const alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

// keys writes a Secret with new keys and a first admin, and applies it, printing the admin's password once, or, with
// --print, prints the Secret instead.
func keys(env *pluginkit.Env, in keysInput) error {
	password, err := random(24)
	if err != nil {
		return err
	}
	hash, err := argon2id(password)
	if err != nil {
		return err
	}
	data := map[string]string{
		keyAdminEmail:    in.AdminEmail,
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
	if data[keyAPIKey], err = settingsKey(); err != nil {
		return err
	}
	if data[keyAPIKeySecret], err = random(64); err != nil {
		return err
	}
	manifest := secretManifest(in.Name, in.Namespace, in.ResourceGroup, data)
	if in.Print {
		_, err := env.Out.Write(manifest)
		return err
	}
	if err := env.Funcdctl.Apply(manifest); err != nil {
		return fmt.Errorf("apply the Secret %s/%s: %w", in.Namespace, in.Name, err)
	}
	_, err = fmt.Fprintf(env.Out, "admin: %s\npassword: %s\nStore the password now: funcd keeps only its hash.\n",
		in.AdminEmail, password)
	return err
}

// secretManifest is a Secret of data, in block-style YAML, each value base64 as a Secret's data holds it.
func secretManifest(name, ns, group string, data map[string]string) []byte {
	var b bytes.Buffer
	fmt.Fprintf(&b, "apiVersion: funcd.io/v1alpha1\nkind: Secret\nmetadata:\n  name: %s\n  namespace: %s\n  resourceGroup: %s\nspec:\n  data:\n",
		name, ns, group)
	for _, k := range []string{keyRaft, keyAPI, keyEncKeys, keyEncActive, keyAdminEmail, keyAdminPassword, keyAPIKey, keyAPIKeySecret} {
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

// settingsKey is the API key rauthy creates at its first start, as BOOTSTRAP_API_KEY holds it: base64 of its JSON
// definition. The settings Function puts a password policy with it, and the access groups are those experiment 0026
// ran with.
func settingsKey() (string, error) {
	type access struct {
		Group  string   `json:"group"`
		Rights []string `json:"access_rights"`
	}
	def, err := json.Marshal(struct {
		Name   string   `json:"name"`
		Access []access `json:"access"`
	}{settingsKeyName, []access{{"Secrets", []string{"read", "update"}}, {"Users", []string{"read", "create", "update"}}}})
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(def), nil
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
