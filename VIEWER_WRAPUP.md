# Prehistorik 2 Viewer Wrap-up Plan Before the Editor

This document captures the point where the viewer should stop growing as a pure viewer and become the stable foundation for an editor.

## Core idea

The viewer should already look and behave like the **read-only mode of the future editor**:

- the same object inspectors,
- the same tile property diagrams,
- the same object selection and map focusing,
- the same domain concepts and names,
- but without mutation, placement tools, undo, or save.

Once that is true, the editor becomes a matter of:

1. enabling controls that are currently read-only,
2. adding placement/move tools,
3. adding validation + undo/redo,
4. implementing serialization and roundtrip save.

---

## 1. Viewer UI that should be considered stable

### Main tabs

- **Level Viewer**
- **Game File Browser**
- future: **Level Editor**

### Level Viewer layout

- left: large scrollable level scene
- right: inspector tabs
  - Tiles
  - Front
  - Info
  - Overlays
  - Mechanics
  - Tile Props

### Interactions already established

- click object in map -> open it in Mechanics
- click object in Mechanics -> focus object in map
- gates cycle source/destination focus on repeated click
- click tile in map or tile browser -> open Tile Props
- map panning by mouse drag
- view opens at player start

These should remain the interaction backbone of the editor.

---

## 2. Read-only inspectors should mirror editor panels

The current Mechanics detail panel should increasingly become a **read-only editor preview**.

### Object panels

#### Enemy inspector
The enemy panel should keep this division:

- **visual identity**
- **behavior type**
- **position / trigger region**
- **authored gameplay values**
- **behavior-specific values**
- **editor-facing flags**
- **advanced/raw**

Checkbox presentation policy:

- dark checkbox: a real level-authored setting that will become editable later,
- grey checkbox: an engine-derived/runtime-locked fact that should remain visible but not look author-controlled.

Examples:

- Expert-only -> future editable
- T9 ground-following patrol flag -> future editable
- Uses trigger rectangle -> grey / behavior-derived
- Spawn cycle controlled by trigger behavior -> grey / behavior-derived
- Runtime initialization fields reset on activation -> grey / RE/debug fact

#### Platform inspector
- visual identity
- human-readable behavior name
- position
- movement parameters
- editor-facing active/slot state where relevant
- raw type and flags hidden in Advanced

#### Item inspector
- human-readable item name
- position
- placement tuning
- active slot state
- raw sprite number in Advanced only

#### Secrets / gates / columns / boss
Keep these as dedicated domain objects, not generic raw tables.

---

## 3. Tile Props should become the Tile Editor template

Tile Props is already the right conceptual panel for the future Tiles Editor.

### It should show

- selected tile identity
- LUT/source information
- physical behavior preview
- side/top/bottom collision semantics
- floor profile / slope semantics
- dynamic `attr2` flags, with friendly labels
- raw attr bytes at the bottom

### The future Tiles Editor should reuse the same visual language

- same physics preview diagram
- same color semantics
  - green = solid/supportive
  - cyan = slippery
  - yellow = drop-through
  - red = deadly
- same shape semantics for flat/inset/slope surfaces

Then add editable controls:

- side behavior dropdown
- top behavior dropdown
- bottom behavior dropdown
- slope kind
- slope base Y
- dynamic tile flags

---

## 4. Viewer completeness still worth finishing before editor work

### A. Finish object behavior visualization

The map overlay should show behavior geometry, not only markers:

#### Monsters
- T2 vertical travel line
- T4 swing radius / arc
- T8 activation box and jump direction metadata where useful
- T9 patrol segment
- T10 trigger rectangle, already mostly present
- T0 trigger region, already mostly present

#### Platforms
- shuttle movement axis and travel range
- falling platform explanation / possible reset marker

#### Gates
- source/destination linkage visualization when selected

This matters for editing because these geometries will later become draggable handles.

### B. Finish semantic names

Mechanics should keep replacing raw IDs with names:

- enemies
- items
- platforms
- special secret modes

Raw numbers should remain available only as a secondary Advanced section.

### C. Finish tile dynamic flags visualization

`attr2` dynamic bits should get clear small icons or labels:

- animated
- front-mask tile
- fly emitter
- step-trigger/change behavior

This should appear both:

- in Level Viewer overlays, if enabled,
- in Tile Props.

### D. Replace remaining bootstrap data with direct game-data extraction where practical

Priority:

1. palettes from `PRE2.EXE`
2. sprite geometry tables from `PRE2.EXE`
3. background routing table from `PRE2.EXE`

The viewer should eventually read as much as possible directly from the supplied game files.

---

## 5. Editor implementation plan after viewer wrap-up

### Step 1: Formal document model

Create a mutable in-memory document separate from the raw parser objects:

- LevelDocument
  - tilemap
  - tile LUT / tile graphics references
  - tile attributes
  - player start / scrolling
  - monsters
  - items
  - platforms
  - gates
  - shifting columns
  - secrets
  - boss

The viewer should be able to render either:

- a parsed immutable level, or
- a mutable LevelDocument.

### Step 2: Commands + undo/redo

Every editor action should become a command:

- paint tile
- move object
- add/remove object
- change monster behavior value
- edit tile physics
- modify gate endpoint

This avoids a rewrite later.

### Step 3: Editing modes

Suggested top-level editor tools:

- **Tiles** — paint/select/eyedropper
- **Objects** — move/select general scene objects
- **Enemies** — enemy-specific add/configure
- **Items**
- **Platforms**
- **Secrets**
- **Gates / Columns**
- **Level Settings**

The current viewer inspectors already define what each tool panel should contain.

### Step 4: Validation layer

Before saving, validate:

- table capacities / slot counts
- coordinates in legal ranges
- object-type-specific record size
- gate source/destination validity
- secret cluster consistency where the editor groups them visually
- platform/monster parameters in accepted value ranges

### Step 5: Serializer + roundtrip tests

Before “real editing”, implement:

- parse -> serialize -> binary equality for untouched files where possible,
- parse -> serialize -> reparse structural equality,
- DOS game launch smoke tests for saved levels.

This is the highest-risk part and should start before large editing UI work.

---

## 6. Practical stopping point for the viewer

The viewer is “editor-ready” when:

- all major visible game entities are rendered and selectable,
- every parsed table has a readable inspector,
- tile properties are understandable without reading raw attrs,
- object behavior geometry is visible,
- names are human-facing wherever reasonably known,
- remaining technical bootstrap tables are clearly isolated,
- data model boundaries are clear enough that mutation can be added without reshaping the whole UI.

At that point, new work should move to **Level Editor** instead of expanding viewer-only features.

---

## 7. UI direction confirmed by the editor-preview refactor

The right-side inspectors should now be treated as **future editor forms**, not prose summaries.

### Mechanics / object inspectors

The intended shape is:

- left: compact object list / slot navigator,
- right: preview plus form controls,
- controls appear as checkboxes, dropdowns, numeric fields, and text fields,
- current milestone is read-only, but controls are intentionally structured so future editing can reuse them directly.

The detail panel should avoid long monospaced explanatory dumps except in explicit **Advanced / raw** sections.

### Tile Props

`Tile Props` is not a tile-browser inspector. It is the **map tile inspector**:

- clicking a tile in the level canvas opens it,
- clicking in the Tiles browser merely reports the tile ID/status and does not steal the inspector,
- the layout should remain compatible with a future Tile Editor:
  - collision/surface dropdowns,
  - dynamic-flag checkboxes,
  - raw attr bytes in an advanced section,
  - physics diagram preview.

### Promotion path

At this point the application should gradually become an editor, not remain a separate viewer that will later be discarded. The next architectural steps are:

1. introduce a mutable `LevelDocument`,
2. keep these exact form panels but unlock controls behind commands,
3. add selection/move/paint tools to the same level canvas,
4. add serializer + undo/redo before broad editing features.

---

## 8. Animated tiles — viewer wrap-up status

Animated tile interpretation is now sufficiently understood for editor planning:

- `attr2 bit 0x80` is a **group-base marker**, not a single-tile boolean,
- the group is always three consecutive tile IDs,
- the runtime cyclically remaps all three phases during rendering,
- the viewer can inspect Frame 1 / Frame 2 / Frame 3,
- Tile Props exposes group base, members, selected phase and visual sequence.

Before editing animated tiles, validation rules should reject:

- base tile > `0xFD`,
- overlapping groups,
- a user trying to set animation on phase 1/2 instead of the group base without explicitly converting the group.

This is no longer a conceptual blocker for starting the editor; it is a serializer/validation concern.
