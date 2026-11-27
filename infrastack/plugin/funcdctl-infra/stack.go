package main

import (
	"encoding/base64"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
)

const (
	outputsSecret = "INFRASTACK_OUTPUTS_SECRET"
	// The alias under which an InfraStack binds its Job (funcd ADR-0358).
	stackJobAlias = "stack"
	sensitive     = "(sensitive)"
)

// infraStack is an InfraStack as funcdctl get prints it: the parts outputs and approve read.
type infraStack struct {
	Spec struct {
		Bindings struct {
			Job []struct {
				Alias string `json:"alias"`
				Job   string `json:"job"`
			} `json:"job"`
		} `json:"bindings"`
	} `json:"spec"`
	Status struct {
		Conditions []struct {
			Type    string `json:"type"`
			Reason  string `json:"reason"`
			Message string `json:"message"`
		} `json:"conditions"`
	} `json:"status"`
}

// stackJob is the Job that the stack binds as stack, which runs its module.
func stackJob(st infraStack, stack, namespace string) (string, error) {
	for _, b := range st.Spec.Bindings.Job {
		if b.Alias == stackJobAlias && b.Job != "" {
			return b.Job, nil
		}
	}
	return "", fmt.Errorf("the InfraStack %s/%s binds no Job as %s", namespace, stack, stackJobAlias)
}

// outputs prints the stack's outputs as funcd ADR-0356's Contracts write them: a NAME VALUE table, the plain outputs
// with their values, then each other key as (sensitive); or with -o json, {"outputs": {...}, "sensitive": [...]}.
func outputs(env *pluginkit.Env, in outputsInput) error {
	plain, secret, err := stackOutputs(env.Funcdctl, in.Stack, in.Namespace)
	if err != nil {
		return err
	}
	if in.Output.JSON() {
		return pluginkit.JSON(env.Out, struct {
			Outputs   map[string]string `json:"outputs"`
			Sensitive []string          `json:"sensitive"`
		}{plain, secret})
	}
	rows := [][]string{{"NAME", "VALUE"}}
	for _, key := range sortedKeys(plain) {
		rows = append(rows, []string{key, plain[key]})
	}
	for _, key := range secret {
		rows = append(rows, []string{key, sensitive})
	}
	return pluginkit.Table(env.Out, rows)
}

// stackOutputs are the keys of the stack's outputs Secret: with its value each one that the Job's contract names
// among its plain outputs, and the others by name only (funcd ADR-0356 Decision 8).
func stackOutputs(ctl *pluginkit.Funcdctl, stack, namespace string) (map[string]string, []string, error) {
	var st infraStack
	if err := ctl.Get(&st, "infrastack", stack, namespace); err != nil {
		return nil, nil, err
	}
	jobName, err := stackJob(st, stack, namespace)
	if err != nil {
		return nil, nil, err
	}
	var job struct {
		Spec struct {
			Bindings struct {
				Config []string `json:"config"`
			} `json:"bindings"`
		} `json:"spec"`
		Status struct {
			Contract struct {
				Output struct {
					Properties struct {
						Outputs struct {
							Properties map[string]any `json:"properties"`
						} `json:"outputs"`
					} `json:"properties"`
				} `json:"output"`
			} `json:"contract"`
		} `json:"status"`
	}
	if err := ctl.Get(&job, "job", jobName, namespace); err != nil {
		return nil, nil, err
	}
	secret := ""
	for _, name := range job.Spec.Bindings.Config {
		var cm struct {
			Spec struct {
				Data map[string]string `json:"data"`
			} `json:"spec"`
		}
		if err := ctl.Get(&cm, "configmap", name, namespace); err != nil {
			return nil, nil, err
		}
		// A later ConfigMap's key wins, as the runtime reads them.
		if v, ok := cm.Spec.Data[outputsSecret]; ok {
			secret = v
		}
	}
	if secret == "" {
		return nil, nil, fmt.Errorf("the Job %s/%s binds no ConfigMap that names %s", namespace, jobName, outputsSecret)
	}
	var s struct {
		Spec struct {
			Data map[string]string `json:"data"`
		} `json:"spec"`
	}
	if err := ctl.Get(&s, "secret", secret, namespace); err != nil {
		return nil, nil, err
	}
	plainNames := job.Status.Contract.Output.Properties.Outputs.Properties
	plain, others := map[string]string{}, []string{}
	for _, key := range sortedKeys(s.Spec.Data) {
		if _, ok := plainNames[key]; !ok {
			others = append(others, key)
			continue
		}
		value, err := decodeValue(s.Spec.Data[key])
		if err != nil {
			return nil, nil, fmt.Errorf("the Secret %s/%s's %s: %w", namespace, secret, key, err)
		}
		plain[key] = value
	}
	return plain, others, nil
}

// decodeValue is a Secret's base64 value as text, skipping any character outside base64's alphabet, as a reader that
// tolerates a line break does.
func decodeValue(b64 string) (string, error) {
	kept := strings.Map(func(r rune) rune {
		if r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '+' || r == '/' || r == '=' {
			return r
		}
		return -1
	}, b64)
	raw, err := base64.StdEncoding.DecodeString(kept)
	if err != nil {
		return "", err
	}
	if !utf8.Valid(raw) {
		return "", errors.New("the value is no UTF-8 text")
	}
	return string(raw), nil
}

var runWord = regexp.MustCompile(`[a-z0-9-]+`)

// awaitedRun is the run that the stack waits on. While it waits for an approval, the reconciler names that run in
// the stack's Ready condition, so the stack itself says which of its Job's runs to approve, whatever other run awaits
// one.
func awaitedRun(ctl *pluginkit.Funcdctl, stack, namespace string) (string, error) {
	var st infraStack
	if err := ctl.Get(&st, "infrastack", stack, namespace); err != nil {
		return "", err
	}
	reason, message := "", ""
	for _, c := range st.Status.Conditions {
		if c.Type == "Ready" {
			reason, message = c.Reason, c.Message
			break
		}
	}
	if reason != "AwaitingApproval" {
		said := fmt.Sprintf("the InfraStack %s/%s awaits no approval", namespace, stack)
		if message != "" {
			said += ": " + message
		}
		return "", errors.New(said)
	}
	jobName, err := stackJob(st, stack, namespace)
	if err != nil {
		return "", err
	}
	var runs []struct {
		Metadata struct {
			Name string `json:"name"`
		} `json:"metadata"`
	}
	if err := ctl.JSON(&runs, "job", "runs", jobName, "-n", namespace, "--phase", "AwaitingApproval", "-o", "json"); err != nil {
		return "", err
	}
	words := runWord.FindAllString(message, -1)
	var named []string
	for _, r := range runs {
		if slices.Contains(words, r.Metadata.Name) {
			named = append(named, r.Metadata.Name)
		}
	}
	if len(named) != 1 {
		return "", fmt.Errorf("the InfraStack %s/%s names no run that awaits approval: %s", namespace, stack, message)
	}
	return named[0], nil
}

// approve approves the run that the stack waits on through funcdctl job approve, which shows the run's plan and asks
// to confirm on the terminal it inherits, so that a person approves exactly the change the stack would make.
func approve(env *pluginkit.Env, in approveInput) error {
	run, err := awaitedRun(env.Funcdctl, in.Stack, in.Namespace)
	if err != nil {
		return err
	}
	return env.Funcdctl.Interactive("job", "approve", run, "-n", in.Namespace)
}
