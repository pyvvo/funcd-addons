package main

import (
	"encoding/json"
	"fmt"

	"github.com/pyvvo/funcd-addons/internal/pluginkit"
)

// urls prints the IdentityProvider's issuer, account and admin URLs, from the host and the scheme its config gives.
func urls(env *pluginkit.Env, in urlsInput) error {
	raw, err := env.Funcdctl.Run("get", "identityprovider", in.Name, "-n", in.Namespace, "-o", "json")
	if err != nil {
		return fmt.Errorf("get the IdentityProvider %s/%s: %w", in.Namespace, in.Name, err)
	}
	var idp struct {
		Spec struct {
			Config struct {
				PublicHost string `json:"publicHost"`
				HTTPS      bool   `json:"https"`
			} `json:"config"`
		} `json:"spec"`
	}
	if err := json.Unmarshal([]byte(raw), &idp); err != nil {
		return fmt.Errorf("read the IdentityProvider: %w", err)
	}
	scheme := "http"
	if idp.Spec.Config.HTTPS {
		scheme = "https"
	}
	base := scheme + "://" + idp.Spec.Config.PublicHost + "/auth/v1"
	_, err = fmt.Fprintf(env.Out, "issuer:  %s/\naccount: %s/account\nadmin:   %s/admin\n", base, base, base)
	return err
}
