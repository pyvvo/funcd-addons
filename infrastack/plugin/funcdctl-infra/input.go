package main

import (
	"fmt"
	"strings"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
)

// pushInput is the module's folder and the ref to push it to.
type pushInput struct {
	Module string
	Ref    string
}

// Validate refuses a ref that names no tag, since the push tags a bundle per platform and the index from it.
func (in pushInput) Validate() error {
	tail := in.Ref[strings.LastIndex(in.Ref, "/")+1:]
	if strings.Contains(in.Ref, "@") || !strings.Contains(tail, ":") {
		return fmt.Errorf("the ref %s names no tag: give <registry>/<repo>:<tag> or oci-layout://<dir>:<tag>", in.Ref)
	}
	return nil
}

// outputsInput is the stack whose outputs to print, and how.
type outputsInput struct {
	Stack     string
	Namespace string
	Output    pluginkit.Format
}

// Validate has nothing to check: funcd refuses a stack it does not have.
func (outputsInput) Validate() error {
	return nil
}

// approveInput is the stack whose awaited run to approve.
type approveInput struct {
	Stack     string
	Namespace string
}

// Validate has nothing to check: funcd refuses a stack it does not have.
func (approveInput) Validate() error {
	return nil
}
