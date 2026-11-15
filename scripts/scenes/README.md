# Scene files

A scene file is what `scripts/record-scene.py` films. The script's own
docstring says what it writes and why; this page is the vocabulary, short.

Top of the file: `name` (names every file), `title`, `base`, `budget_seconds`,
`width`/`height` (1280×720), `captions`, `lesson`, `part`, `notes`,
`allow_writes` (loopback only), `[plant]` (a scratch plant of the take's own),
and `type_delay_seconds` — seconds between typed keys, 0.085 by default
(about twelve a second), never under 0.05.

Each `[[step]]` has a `do`:

| `do` | needs | may have |
|---|---|---|
| `goto` | `url` | `settle` |
| `click` | `selector` | |
| `fill` | `selector`, `text` | |
| `choose` | `selector` (a `<select>`), `value` | |
| `wait_for` | `selector` with `text` / `attribute`+`equals` / `js`, or `js` | `timeout` |
| `say` | `line` | `hold`, `at`, `label`, `style` |
| `hold` | | `seconds` |
| `mark` | `name` | |
| `slide` | `name`, `title` | `lines`, `command`, `start_at`, `stop_after`, `hold` |

Any step may also have `part` and `scroll_to` (with `scroll_block`,
`scroll_offset`, `scroll_text`).

`click`, `fill` and `choose` move a drawn cursor to the thing and press it
with a ripple. A `<select>` cannot be opened by a script, so on `choose` the
cursor presses the list and the value changes.

A `say` with `at` (a selector, as `click` takes) draws a box round that thing
as the caption appears, for the line's hold, and through any `mark` straight
after it, so those stills show the box; `label` puts a few words on a tab
at the box's top left (below it when the box is at the top of the frame,
inside it when the box fills the frame); `style = "spotlight"` dims the rest
of the frame instead. A thing off the frame, or under the page's sticky
header or the caption bar, is scrolled clear first; a thing not on the page
refuses the take, naming the step. Point `at` something smaller than the
frame. `label` or `style` without `at` is refused.
