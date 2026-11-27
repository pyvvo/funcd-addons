//go:build ignore

// Stamp prints the -ldflags that stamp funcdctl-infra with the opentofu1 image of the Addon it ships beside (funcd
// ADR-0355 Decision 1), as `just plugins` builds it: `go run stamp.go [addon.yaml]`, the Addon by default
// infrastack/app/addon.yaml from the repository's root.
package main

import (
	"fmt"
	"os"

	yaml "go.yaml.in/yaml/v3"
)

func main() {
	path := "infrastack/app/addon.yaml"
	if len(os.Args) > 1 {
		path = os.Args[1]
	}
	image, err := addonImage(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, "stamp:", err)
		os.Exit(1)
	}
	fmt.Printf("-X main.image=%s\n", image)
}

// addonImage is the image of the Addon's opentofu1 runtime.
func addonImage(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	var addon struct {
		Spec struct {
			Runtimes []struct {
				Name  string `yaml:"name"`
				Image string `yaml:"image"`
			} `yaml:"runtimes"`
		} `yaml:"spec"`
	}
	if err := yaml.Unmarshal(raw, &addon); err != nil {
		return "", fmt.Errorf("%s: %w", path, err)
	}
	for _, r := range addon.Spec.Runtimes {
		if r.Name == "opentofu1" && r.Image != "" {
			return r.Image, nil
		}
	}
	return "", fmt.Errorf("%s ships no opentofu1 runtime with an image", path)
}
