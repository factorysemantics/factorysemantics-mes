"""Inbound events: what other systems tell this MES that it cannot observe.

The OPC agent is the MES's eyes. This package is its ears. A technician
labels a stop, an inspector records a check, an operator types a count —
into whatever system the plant already runs — and those facts reach the MES
as data supplied by that system, recorded as such and never as observation.

`contract` is the shape. `folder` is the first driver that fills it: files
dropped in a folder. A SQL poller and an MQTT subscriber are the same three
models arriving over a different transport.
"""
