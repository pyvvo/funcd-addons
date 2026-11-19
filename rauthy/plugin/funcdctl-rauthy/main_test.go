package main

import (
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"golang.org/x/crypto/argon2"
)

func TestArgon2idIsAPHCStringOfThePassword(t *testing.T) {
	t.Parallel()
	hash, err := argon2id("correct horse")
	if err != nil {
		t.Fatal(err)
	}
	m := regexp.MustCompile(`^\$argon2id\$v=19\$m=65536,t=3,p=4\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)$`).FindStringSubmatch(hash)
	if m == nil {
		t.Fatalf("not a PHC argon2id string: %s", hash)
	}
	salt, _ := base64.RawStdEncoding.DecodeString(m[1])
	want := argon2.IDKey([]byte("correct horse"), salt, argonTime, argonMemoryKiB, argonThreads, argonKeyLen)
	if m[2] != base64.RawStdEncoding.EncodeToString(want) {
		t.Fatal("the hash is not the password's")
	}
}

func TestKeysPrintsEverySecretKey(t *testing.T) {
	t.Parallel()
	var out bytes.Buffer
	if err := keys([]string{"--admin-email", "admin@example.com", "--print", "--name", "k", "-n", "team"}, &out); err != nil {
		t.Fatal(err)
	}
	s := out.String()
	for _, k := range []string{keyRaft, keyAPI, keyEncKeys, keyEncActive, keyAdminEmail, keyAdminPassword, "name: k", "namespace: team"} {
		if !strings.Contains(s, k) {
			t.Errorf("the Secret lacks %s:\n%s", k, s)
		}
	}
}

func TestKeysNeedsAnAdminEmail(t *testing.T) {
	t.Parallel()
	if err := keys([]string{"--print"}, &bytes.Buffer{}); err == nil {
		t.Fatal("keys ran with no admin email")
	}
}

func TestURLsReadTheIdentityProvidersConfig(t *testing.T) {
	dir := t.TempDir()
	fake := filepath.Join(dir, "funcdctl")
	script := "#!/bin/sh\necho '{\"spec\":{\"config\":{\"publicHost\":\"login.example.com\",\"https\":true}}}'\n"
	if err := os.WriteFile(fake, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FUNCDCTL", fake)
	var out bytes.Buffer
	if err := urls([]string{"login"}, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "issuer:  https://login.example.com/auth/v1/") ||
		!strings.Contains(out.String(), "admin:   https://login.example.com/auth/v1/admin") {
		t.Fatalf("unexpected URLs:\n%s", out.String())
	}
}

func TestRandomIsAlphanumericOfItsLength(t *testing.T) {
	t.Parallel()
	s, err := random(48)
	if err != nil {
		t.Fatal(err)
	}
	if len(s) != 48 || strings.Trim(s, alnum) != "" {
		t.Fatalf("random(48) = %q", s)
	}
}
