# Scene files

A scene file is what `scripts/record-scene.py` films. The script's own
docstring says what it writes and why; this page is the vocabulary, short.

Top of the file: `name` (names every file), `title`, `base`, `budget_seconds`,
`width`/`height` (1280×720), `captions`, `lesson`, `part`, `notes`,
`allow_writes` (loopback only), `[plant]` (a scratch plant of the take's own),
and `type_delay_seconds` — seconds between typed keys, 0.085 by default
(about twelve a second), never under 0.05.
A take that is not a lesson may set `gif_seconds`: its GIF is then that many
seconds from the start, a preview, while the mp4 stays the whole take — for a
take of minutes, whose GIF would never come in under 10,000,000 bytes.

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
just outside the box, never over the page's own writing: above the box at
its left end, else above at its right end, else below it (left, then right),
else beside its right edge, else beside its left. A spot under the sticky
header, on the caption bar, in the narrator's corner, off the frame, on the
box, or over any text of the page (a card's title above the box) is passed
over. Only when no spot is clear may the label cover the boxed thing's own
caption (its `<label>`, or the short word over a value in a grid) and
nothing else; a label with no clear spot refuses the take, naming the step, so keep
labels short. `marks.json` says where it sat (`label_box`). `style =
"spotlight"` dims the rest of the frame instead. A thing off the frame, or under the page's sticky
header or the caption bar, or reaching into the narrator's corner at the
bottom right (240 by 200), is scrolled clear first; a thing not on the page
refuses the take, naming the step. Point `at` something smaller than the
frame. `label` or `style` without `at` is refused. A box, a label or a press
that still lands in the narrator's corner fails the take, as a caption there does.

## `film/`

A scene in `film/` is the screen take of a longer film, not a clip of the
series: one take of a few minutes with marks, which a narrator cuts, voices
and lays other footage over. It is kept out of the top level for the same
reason `course/` is: a clip there plans 45 seconds or less, and the test of
that budget reads the top level only.
