"""The OpenTofu runtime's entry (funcd ADR-0344): funcd mounts a Job's artifact, its OpenTofu module, and names the
bundle's entry file in FUNCD_ARTIFACT; the shim loads the runtime's own handler instead, which finds the module's folder
in INFRASTACK_MODULE_DIR."""

import os

from funcd_shim.shim import main

artifact = os.environ.get("FUNCD_ARTIFACT", "")
os.environ["INFRASTACK_MODULE_DIR"] = os.path.dirname(artifact) if artifact else "/var/funcd/artifact"
os.environ["FUNCD_ARTIFACT"] = "/opt/infrastack/handler.py"

raise SystemExit(main())
