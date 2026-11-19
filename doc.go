// Package addons holds the add-ons built outside funcd that an admin registers on a running funcd (funcd
// ADR-0332), one folder each: its Addon, its examples and its funcdctl plugin. funcd pins this module in its go.mod,
// as it pins funcd-functions (ADR-0141), and reads its files from the module cache: its tests decode every Addon here,
// and its lanes register them.
package addons

// Module is this module's path, as funcd's go.mod requires it.
const Module = "github.com/pyvvo/funcd-addons"
