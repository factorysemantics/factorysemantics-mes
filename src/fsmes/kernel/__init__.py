"""The kernel: master data, routings, orders, dispatch, execution, audit, auth.

Always present; every other capability is an optional module hanging off the
hooks the kernel publishes. The dependency rule inherited from MES-TWIN, and
the reason that codebase could be ported at all:

    domain <- services <- {api, connect, modules, cli}

No lower layer may import a higher one. A service never imports the API; the
domain never imports a service.
"""
