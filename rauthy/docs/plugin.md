# funcdctl rauthy

The commands of `funcdctl-rauthy`, the add-on's
[funcdctl plugin](https://github.com/pyvvo/funcd/blob/main/docs/adr/0319-funcdctl-runs-kubectl-style-plugins.md),
which reaches funcd through `$FUNCDCTL`. The README's
[Declare an identity provider](../README.md#declare-an-identity-provider) shows them at work.

## Reference

The usage and the flags of each command, as `funcdctl rauthy <command> --help` prints them:

<!-- funcdctl rauthy reference: just plugin-docs -->

### funcdctl rauthy keys

Apply a Secret with new cluster and encryption keys and a first admin, and print the admin's password once

```
funcdctl rauthy keys [flags]
```

#### Options

```
      --admin-email string      the first admin's email (required)
      --name string             the Secret's name (default "rauthy-keys")
  -n, --namespace string        the namespace (default "default")
      --print[=true|false]      print the Secret instead of applying it
      --resource-group string   the Secret's resource group (default "default")
```

### funcdctl rauthy urls

Print the IdentityProvider's issuer, account and admin URLs

```
funcdctl rauthy urls <identityprovider> [flags]
```

#### Options

```
  -n, --namespace string   the namespace (default "default")
```

<!-- end funcdctl rauthy reference -->
