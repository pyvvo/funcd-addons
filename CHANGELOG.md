# Changelog

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
