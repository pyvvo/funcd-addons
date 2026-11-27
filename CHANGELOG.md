# Changelog

## [0.10.0](https://github.com/pyvvo/funcd-addons/compare/v0.9.0...v0.10.0) (2026-11-27)


### ⚠ BREAKING CHANGES

* **plugins:** a release attaches funcdctl-infra as one archive per platform, funcdctl-infra-<os>-<arch>.tar.gz, as it attaches funcdctl-rauthy, instead of one script. The plugin no longer needs Python.

### Features

* **plugins:** the funcdctl plugins share a Go kit, and funcdctl-infra is a Go command ([#41](https://github.com/pyvvo/funcd-addons/issues/41)) ([97a2399](https://github.com/pyvvo/funcd-addons/commit/97a239914acb35ec39753ff5547d51a9d6c72481))

## [0.9.0](https://github.com/pyvvo/funcd-addons/compare/v0.8.3...v0.9.0) (2026-11-27)


### Features

* **infrastack:** funcdctl infra approve approves the run that a stack waits on ([#38](https://github.com/pyvvo/funcd-addons/issues/38)) ([4da95db](https://github.com/pyvvo/funcd-addons/commit/4da95db24e7b35c85996342af976f45b9692a8d1))

## [0.8.3](https://github.com/pyvvo/funcd-addons/compare/v0.8.2...v0.8.3) (2026-11-25)


### Bug Fixes

* **infrastack:** funcdctl infra push streams its files, so it works with a nerdctl in a VM ([#36](https://github.com/pyvvo/funcd-addons/issues/36)) ([4ee8930](https://github.com/pyvvo/funcd-addons/commit/4ee8930e3fa69d0e6cce99ea077c1edf5573191c))

## [0.8.2](https://github.com/pyvvo/funcd-addons/compare/v0.8.1...v0.8.2) (2026-11-25)


### Bug Fixes

* **infrastack:** the InfraStack Addon and funcdctl-infra pin the OpenTofu image that v0.8.1 published ([#34](https://github.com/pyvvo/funcd-addons/issues/34)) ([7c0d721](https://github.com/pyvvo/funcd-addons/commit/7c0d72191aef7224ecaaf3498baee2a123e01f8d))

## [0.8.1](https://github.com/pyvvo/funcd-addons/compare/v0.8.0...v0.8.1) (2026-11-25)


### Bug Fixes

* **infrastack:** the OpenTofu image carries the shim that reaches funcd under funcdctl dev ([#32](https://github.com/pyvvo/funcd-addons/issues/32)) ([1b8d93c](https://github.com/pyvvo/funcd-addons/commit/1b8d93c56692561894a0e449309f49ea61df8d26))

## [0.8.0](https://github.com/pyvvo/funcd-addons/compare/v0.7.0...v0.8.0) (2026-11-25)


### ⚠ BREAKING CHANGES

* **addons:** rauthy's and InfraStack's kinds bring their Functions as images ([#30](https://github.com/pyvvo/funcd-addons/issues/30))

### Features

* **addons:** rauthy's and InfraStack's kinds bring their Functions as images ([#30](https://github.com/pyvvo/funcd-addons/issues/30)) ([140dd91](https://github.com/pyvvo/funcd-addons/commit/140dd911446f37d99eb8fe333f8be376296bc496))


### Bug Fixes

* **addons:** the Functions that only funcd calls have no address ([#28](https://github.com/pyvvo/funcd-addons/issues/28)) ([9656d70](https://github.com/pyvvo/funcd-addons/commit/9656d700daef508f0d054d7caf47ab6f36104e1a))

## [0.7.0](https://github.com/pyvvo/funcd-addons/compare/v0.6.1...v0.7.0) (2026-11-24)


### Features

* **lanes:** `just lanes` runs every add-on lane in one shared VM ([#25](https://github.com/pyvvo/funcd-addons/issues/25)) ([66e692d](https://github.com/pyvvo/funcd-addons/commit/66e692d1168abf94b83ba84ef61ae31b7048d783))

## [0.6.1](https://github.com/pyvvo/funcd-addons/compare/v0.6.0...v0.6.1) (2026-11-24)


### Bug Fixes

* **infrastack:** a module whose every variable is sensitive takes void variables, so a run's input sets none (funcd ADR-0355, amended) ([#22](https://github.com/pyvvo/funcd-addons/issues/22)) ([7b07ef0](https://github.com/pyvvo/funcd-addons/commit/7b07ef0c723bfa5ba50775840c8ea58068b1da62))

## [0.6.0](https://github.com/pyvvo/funcd-addons/compare/v0.5.1...v0.6.0) (2026-11-24)


### Features

* **infrastack:** examples of a module that takes its sensitive variable from a Secret, and of a Function that reads a stack's outputs ([#19](https://github.com/pyvvo/funcd-addons/issues/19)) ([0c07501](https://github.com/pyvvo/funcd-addons/commit/0c07501d7a7bf6249a06abfd77f95accbfedc6b3))

## [0.5.1](https://github.com/pyvvo/funcd-addons/compare/v0.5.0...v0.5.1) (2026-11-24)


### Bug Fixes

* **infrastack:** the InfraStack Addon and funcdctl-infra pin the OpenTofu image that v0.5.0 published ([#17](https://github.com/pyvvo/funcd-addons/issues/17)) ([33e9c22](https://github.com/pyvvo/funcd-addons/commit/33e9c227099e98876b5c8cb7e1091efb2f7cb07c))

## [0.5.0](https://github.com/pyvvo/funcd-addons/compare/v0.4.1...v0.5.0) (2026-11-24)


### ⚠ BREAKING CHANGES

* **infrastack:** every output of an apply reaches its readers through the Secret its Job owns (funcd ADR-0356)

### Features

* **infrastack:** an applied stack is checked for drift on its config.drift schedule (F141) ([dedf8da](https://github.com/pyvvo/funcd-addons/commit/dedf8daff3e6c0b9d123f004023a4715f8d84678))
* **infrastack:** every output of an apply reaches its readers through the Secret its Job owns (funcd ADR-0356) ([dedf8da](https://github.com/pyvvo/funcd-addons/commit/dedf8daff3e6c0b9d123f004023a4715f8d84678))
* **infrastack:** funcdctl infra push builds a module's bundles with their providers and a contract, a plan run answers what would change, and funcdctl infra outputs prints a stack's outputs (funcd ADR-0355, ADR-0356) ([dedf8da](https://github.com/pyvvo/funcd-addons/commit/dedf8daff3e6c0b9d123f004023a4715f8d84678))

## [0.4.1](https://github.com/pyvvo/funcd-addons/compare/v0.4.0...v0.4.1) (2026-11-24)


### Bug Fixes

* **addons:** the InfraStack and tunnel Addons pin the images that v0.4.0 published ([#13](https://github.com/pyvvo/funcd-addons/issues/13)) ([883915b](https://github.com/pyvvo/funcd-addons/commit/883915b23af95016d54b14c0762e2c7a5dcc0368))

## [0.4.0](https://github.com/pyvvo/funcd-addons/compare/v0.3.0...v0.4.0) (2026-11-24)


### Features

* **infrastack:** an OpenTofu runtime for Jobs that applies only the approved saved plan (funcd ADR-0344, ADR-0348) ([4c47f2e](https://github.com/pyvvo/funcd-addons/commit/4c47f2eec1e24ca4b73b0f8a733e810e4153056b))
* **infrastack:** the InfraStack add-on, an engineless kind whose Function drives one Job per stack (funcd ADR-0345, ADR-0349) ([4c47f2e](https://github.com/pyvvo/funcd-addons/commit/4c47f2eec1e24ca4b73b0f8a733e810e4153056b))
* **tunnel:** the tunnel add-on, frpc as an engine that hands visitors to funcd's edge (funcd ADR-0352 to ADR-0354) ([36ff3df](https://github.com/pyvvo/funcd-addons/commit/36ff3df638c5432cfe3cd6052d00766b42dd9105))

## [0.3.0](https://github.com/pyvvo/funcd-addons/compare/v0.2.0...v0.3.0) (2026-11-24)


### ⚠ BREAKING CHANGES

* **rauthy:** the Addon needs a funcd that implements ADR-0344; an older funcd refuses it.

### Features

* **rauthy:** rauthy's Addon ships its engine as a runtime, whose attributes hold its kind (funcd ADR-0344) ([#8](https://github.com/pyvvo/funcd-addons/issues/8)) ([27689e1](https://github.com/pyvvo/funcd-addons/commit/27689e143c3e1b26e24f00da4a8d2bb5887d8036))

## [0.2.0](https://github.com/pyvvo/funcd-addons/compare/v0.1.1...v0.2.0) (2026-11-22)


### Features

* **rauthy:** the password policy settings kind and its Function, and rauthy as an App template (funcd ADR-0337, ADR-0340, ADR-0341) ([#6](https://github.com/pyvvo/funcd-addons/issues/6)) ([4f2c64b](https://github.com/pyvvo/funcd-addons/commit/4f2c64b6399e865de2d22c82c1edd8cf9b99c9b2))

## [0.1.1](https://github.com/pyvvo/funcd-addons/compare/v0.1.0...v0.1.1) (2026-11-19)


### Bug Fixes

* **rauthy:** rauthy starts when publicHost names no port, as the README says ([#3](https://github.com/pyvvo/funcd-addons/issues/3)) ([77c838a](https://github.com/pyvvo/funcd-addons/commit/77c838a514bd3633497e9a4a2a31a93e723b4a66))

## 0.1.0 (2026-11-19)


### Features

* **rauthy:** the rauthy add-on, its funcdctl plugin, and CI with an end-to-end run ([#1](https://github.com/pyvvo/funcd-addons/issues/1)) ([08bfa6c](https://github.com/pyvvo/funcd-addons/commit/08bfa6c23afa97d3bccde2b273904913ba05ffdd))
