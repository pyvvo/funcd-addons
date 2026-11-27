package main

import (
	"errors"
	"strings"
)

// keysInput is the Secret to write and its first admin.
type keysInput struct {
	AdminEmail    string
	Name          string
	Namespace     string
	ResourceGroup string
	Print         bool
}

// Validate refuses an admin email with no @, which rauthy would refuse at its first start.
func (in keysInput) Validate() error {
	if !strings.Contains(in.AdminEmail, "@") {
		return errors.New("--admin-email is required")
	}
	return nil
}

// urlsInput is the IdentityProvider whose URLs to print.
type urlsInput struct {
	Name      string
	Namespace string
}

// Validate has nothing to check: funcd refuses an IdentityProvider it does not have.
func (urlsInput) Validate() error {
	return nil
}
