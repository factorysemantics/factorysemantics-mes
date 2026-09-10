"""Inbound events: what other systems tell this MES that it cannot observe.

The OPC agent is the MES's eyes. This package is its ears. A technician
labels a stop, an inspector records a check, an operator types a count —
into whatever system the plant already runs — and those facts reach the MES
as data supplied by that system, recorded as such and never as observation.

`contract` is the shape. Two drivers fill it: `folder`, files dropped in a
folder, and `sql`, a read-only query run on a schedule against the database
that other system already keeps. An MQTT subscriber would be a third. None of
them changes what a row means when it lands - that is the point of there
being a contract at all.
"""
