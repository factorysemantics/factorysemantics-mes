"""The unified-namespace publisher: MES events onto an MQTT broker.

`topics` turns the equipment model into an ISA-95 topic; `envelope` says what
one event looks like on the wire; `transport` is the broker (or the log);
`publisher` is the worker loop. The MES-side queue lives in
`fsmes.services.uns`, the way ERP logic lives in `fsmes.services.erp`.
"""
