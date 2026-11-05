# A draft tag map from a project export from Kepware

*How-to. From a KEPServerEX project file to a draft tag map to
`fsmes opc-verify`, on your own PC.*

If your plant's PLCs are behind KEPServerEX, its project file already lists
every channel, device and tag, with each tag's type. `fsmes tagmap
from-kepware` reads that file and writes a **draft** of the MES's tag map from
it. It saves you typing a node id per tag. It does not save you from reading
the draft: which tag is the state and which tags count good parts is guessed
from the tag names, and every guess says so.

The command reads a file and writes a file. Nothing in the export leaves your
PC, and no server is touched.

## 1. Save the project as JSON

In the KEPServerEX Configuration, choose **File → Save As**, choose
**JSON** as the format, and decline encryption. An encrypted or `.opf` project
cannot be read.

## 2. Draft the map

Pick the channel whose devices are the machines of one line:

```
fsmes tagmap from-kepware line.json --channel Line --out tag_map_kepware.json
```

If each PLC has a channel of its own, repeat `--channel` once per channel.
A device name that two of those channels share (Kepware's default `Device1`)
becomes two machines named `<Channel>-<Device>`. Add
`--device PRESS-1 --device WASH-1` to draft only some devices. A channel or
device name that is not in the file is refused, and the error lists the names
that are.

The worked example is the invented two-station line the tests use,
[`tests/fixtures/kepware/toy_project.json`](https://github.com/factorysemantics/factorysemantics-mes/blob/main/tests/fixtures/kepware/toy_project.json):
a `PRESS-1` with tools A and B, a `WASH-1`, and a `Scratch` channel that is not
a line. On it the command prints:

```
  note: PRESS-1: a Kepware export has no cycle time; set cycle_seconds (...)
  note: WASH-1: a Kepware export has no cycle time; set cycle_seconds (...)
  PRESS-1: 12 tags placed, each a guess; 2 unmapped
  WASH-1: 6 tags placed, each a guess; 0 unmapped
Wrote tag_map_kepware.json: 2 machines from 2 devices; 20 tags, 18 placed, 2 unmapped.
It is a draft: read every `_guess`, place what is under `_unmapped`, set cycle_seconds and reason_map.
Next: `fsmes opc-verify` against the live server, with MES_TAG_MAP_FILE=tag_map_kepware.json.
```

Placed plus unmapped is always the export's own tag count. No tag is dropped
without being listed.

## 3. Read the draft

Each device becomes one machine. Each placed tag is addressed by the node id
Kepware's OPC UA server gives it, `ns=2;s=<Channel>.<Device>[.<Group>...].<Tag>`.
Each placed tag also has a line under `_guess` saying why it was put there:

```json
"_guess": {
  "DowntimeCode": "the state and the stop's reason: name contains Downtime, read as 0 while it runs and the stop's number otherwise; what each number means is not in the export, so write it into reason_map",
  "Good_A": "counts good: name contains Good and it is a whole number (DWord)",
  "Reject_A": "counts scrap: name contains Reject and it is a whole number (Word)",
  "TotalCount": "recorded, not booked: name contains Count, and this device also has a Good counter, which a total usually already includes",
  "Status": "recorded only: name contains Status, a state word whose values only the plant knows; to read the state from it, set state_tag and write its values into state_map"
}
```

The rules, in order:

| The tag is | named with | it becomes |
|---|---|---|
| a whole number | Downtime, Fault, Alarm, Error | the **state** and the stop's **reason** (`0` = running, anything else = down). The word earliest in that list wins, then a name with Code in it, then the tag first in the export; the others are recorded |
| a whole number | Heartbeat, Watchdog | recorded only |
| a whole number | Reject, Scrap, NG, Bad | a **scrap** counter |
| a whole number | Good, OK, Pass | a **good** counter |
| a whole number | Count, Counter, Total, Produced, Lifetime | a **good** counter if the device has no Good counter, otherwise recorded only, so nothing is counted twice |
| a good or scrap counter | no Count, Counter or Total, beside ones that have it | recorded only: most likely a flag (OK to process), not a tally |
| a good or scrap counter | Total, Lifetime | recorded only if the device has other counters of that kind, which it usually adds up; totals with nothing else beside them, one per side say, are all counted |
| a number or on/off | State, Status, Mode, Run | recorded only: what its values mean is the plant's to say |
| text (String) | ID, Serial, Lot, Batch, SN, Code, Name, Recipe | recorded as text |
| a real number (Float, Double) | anything | the first one is the **process value**; the rest are recorded |
| anything else | | listed under `_unmapped` with its type and node id |

Keys that start with `_` are notes. The MES, `fsmes pack check` and
`fsmes pack apply` ignore them, so you can keep them in the file while you
work.

## 4. What the export cannot tell you, and you must

- **Cycle times.** They are not in a KEPServerEX project, so the draft has no
  `cycle_seconds`. Set one per machine. Until you do, `fsmes seed-line` leaves
  OEE performance unknown. `fsmes pack apply` still uses the loader's 5 s
  default.
- **What the fault numbers mean.** A machine whose state is read from a fault
  code shows "FaultCode 41" on a stop until you write the plant's reason codes
  into `reason_map`, e.g. `{"41": "tool_crash"}`.
- **What a state word's values mean.** If a machine has a real status word,
  set `state_tag` to it and write its values into `state_map`; the draft will
  not guess them.
- **The unmapped tags.** Move any you want into `nodes` and `extra_tags` by
  hand, or leave them out on purpose.
- **Everything is read-only.** `order_tag` is null on every machine. The MES
  observes the line and writes nothing to it.

## 5. Prove it against the live server

```
set MES_TAG_MAP_FILE=tag_map_kepware.json
fsmes opc-verify
```

`opc-verify` resolves every mapped node, reads each tag twice, and says per
machine what was readable, what changed, and which state values map. Run it
while the line is running. Connecting to a server you do not own is covered
in [Connecting read-only to an OPC UA server you do not own](opc-readonly.md).

## Where the export's shape was checked

The command reads only these parts of the file: `project.channels[]` →
`devices[]` → `tags[]` and `tag_groups[]` (nested), each object's
`common.ALLTYPES_NAME`, a tag's `servermain.TAG_DATA_TYPE`, and, for the
draft's comment line, the channel's `servermain.MULTIPLE_TYPES_DEVICE_DRIVER`.

- The collections are the Configuration API's own endpoints,
  `/config/v1/project/channels/{channel}/devices/{device}/tag_groups/{group}/tags/{tag}`.
  This is the route in PTC's .NET SDK reference for
  [`DeviceTagGroup`](https://ptcinc.github.io/Kepware-ConfigAPI-SDK-dotnet/api/Kepware.Api.Model.DeviceTagGroup.html)
  and [`Tag`](https://ptcinc.github.io/Kepware-ConfigAPI-SDK-dotnet/api/Kepware.Api.Model.Tag.html).
- `common.ALLTYPES_NAME`, `common.ALLTYPES_DESCRIPTION` and
  `servermain.MULTIPLE_TYPES_DEVICE_DRIVER` appear in PTC's
  [Configuration API project example](https://support.ptc.com/help/kepware/kepware_edge/en/kepware/kepware-edge/config-api-example-project.html).
  `servermain.TAG_ADDRESS` appears in PTC's
  [Python SDK](https://github.com/PTCInc/Kepware-ConfigAPI-SDK-Python).
- The data type numbers (0 String, 1 Boolean, 3 Byte, 4 Short, 5 Word, 6 Long,
  7 DWord, 8 Float, 9 Double) are from EMQX's
  [Kepware migration guide](https://docs.emqx.com/en/neuronex/latest/configuration/driver-migration-tool/kepware-to-neuron.html),
  which also documents *File → Save As → JSON*. Code 2 is taken to be Char.
  Any other number is written as "type *n*" and is never given a role.

If your server's export differs, the command refuses the file and says what
it looked for. Please open an issue with the message, and leave out your tag
names if they are private.
