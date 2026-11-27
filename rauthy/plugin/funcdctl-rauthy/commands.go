package main

import (
	"github.com/pyvvo/funcd-addons/internal/pluginkit"
	"github.com/spf13/pflag"
)

var keysCommand = pluginkit.Spec[keysInput]{
	Use:   "keys",
	Short: "Apply a Secret with new cluster and encryption keys and a first admin, and print the admin's password once",
	Flags: func(fs *pflag.FlagSet, in *keysInput) {
		fs.StringVar(&in.AdminEmail, "admin-email", "", "the first admin's email")
		fs.StringVar(&in.Name, "name", "rauthy-keys", "the Secret's name")
		pluginkit.Namespace(fs, &in.Namespace)
		fs.StringVar(&in.ResourceGroup, "resource-group", "default", "the Secret's resource group")
		fs.BoolVar(&in.Print, "print", false, "print the Secret instead of applying it")
	},
	Required: []string{"admin-email"},
	Run:      keys,
}

var urlsCommand = pluginkit.Spec[urlsInput]{
	Use:   "urls <identityprovider>",
	Short: "Print the IdentityProvider's issuer, account and admin URLs",
	Flags: func(fs *pflag.FlagSet, in *urlsInput) {
		pluginkit.Namespace(fs, &in.Namespace)
	},
	Args: func(in *urlsInput, args []string) {
		in.Name = args[0]
	},
	Run: urls,
}
