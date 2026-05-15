from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from PIL import Image, ImageDraw, ImageTk

from pre2lib.formats import (
    Bonus,
    Gate,
    Item,
    Monster,
    LEVEL_IDS,
    EXPECTED_MONSTER_LENGTHS,
    Platform,
    Pre2FormatError,
    load_level,
    load_union_tiles,
    load_front_tiles,
    load_background_bitmap,
    load_palettes,
    unpack_file,
    describe_tile_physics,
    describe_tile_side_behavior,
    describe_tile_top_behavior,
    describe_tile_floor_profile,
    serialize_level,
    save_level_sqz,
    monster_extra_defaults,
)
from pre2lib.renderer import render_level_image, render_level_chunk_image, render_tile_image, render_tile_atlas, render_front_tile_atlas, render_background_image
from pre2lib.sprites import (
    SpriteResolver,
    load_sprite_tables,
    load_sprites_blob,
    render_sprite_image,
    render_sprite_atlas,
)
from pre2lib.labels import (
    monster_visual_name,
    monster_display_name,
    MONSTER_PLACEMENT_DEFAULT_BEHAVIOR,
    MONSTER_PLACEMENT_VISUALS,
    ITEM_VISUAL_NAMES,
    PLATFORM_VISUAL_NAMES,
    item_visual_name,
    platform_visual_name,
    platform_behavior_name,
    platform_display_name,
)


class ScrollableCanvas(ttk.Frame):
    def __init__(self, master: tk.Misc, **kwargs):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, **kwargs)
        self.xscroll = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.yscroll = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.canvas.configure(xscrollcommand=self.xscroll.set, yscrollcommand=self.yscroll.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.yscroll.grid(row=0, column=1, sticky="ns")
        self.xscroll.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.photo = None
        self.image_item = None
        self.tiled_photos: list[ImageTk.PhotoImage] = []
        self.tiled_image_items: list[int] = []
        # Incremental level renderer: keyed by (chunk_x, chunk_y) in chunk-grid units.
        self.chunk_photos: dict[tuple[int, int], ImageTk.PhotoImage] = {}
        self.chunk_image_items: dict[tuple[int, int], int] = {}
        self.viewport_background_source: Image.Image | None = None
        self.viewport_background_resized: Image.Image | None = None
        self.viewport_background_photo: ImageTk.PhotoImage | None = None
        self.viewport_background_item: int | None = None
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _xview(self, *args) -> None:
        self.canvas.xview(*args)
        self.position_viewport_background()

    def _yview(self, *args) -> None:
        self.canvas.yview(*args)
        self.position_viewport_background()

    def _on_canvas_configure(self, _event=None) -> None:
        self.rerender_viewport_background()

    def set_viewport_background(self, image: Image.Image | None) -> None:
        self.viewport_background_source = image.copy() if image is not None else None
        self.rerender_viewport_background()

    def rerender_viewport_background(self) -> None:
        if self.viewport_background_source is None:
            if self.viewport_background_item is not None:
                self.canvas.delete(self.viewport_background_item)
            self.viewport_background_item = None
            self.viewport_background_resized = None
            self.viewport_background_photo = None
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        resized = self.viewport_background_source.resize((width, height), Image.Resampling.NEAREST)
        self.viewport_background_resized = resized
        self.viewport_background_photo = ImageTk.PhotoImage(resized)
        if self.viewport_background_item is not None:
            self.canvas.delete(self.viewport_background_item)
        self.viewport_background_item = self.canvas.create_image(
            self.canvas.canvasx(0),
            self.canvas.canvasy(0),
            image=self.viewport_background_photo,
            anchor="nw",
            tags=("viewport_background",),
        )
        self.canvas.tag_lower(self.viewport_background_item)

    def position_viewport_background(self) -> None:
        if self.viewport_background_item is None:
            return
        self.canvas.coords(self.viewport_background_item, self.canvas.canvasx(0), self.canvas.canvasy(0))
        self.canvas.tag_lower(self.viewport_background_item)

    def set_image(self, photo: ImageTk.PhotoImage) -> None:
        """Set a single Tk image. Suitable for atlases and modest canvases."""
        self.photo = photo
        self.tiled_photos.clear()
        self.tiled_image_items.clear()
        self.chunk_photos.clear()
        self.chunk_image_items.clear()
        self.canvas.delete("all")
        self.image_item = self.canvas.create_image(0, 0, anchor="nw", image=photo, tags=("base_image",))
        self.canvas.configure(scrollregion=(0, 0, photo.width(), photo.height()))
        self.rerender_viewport_background()
        if self.image_item is not None:
            self.canvas.tag_raise(self.image_item)

    def set_pil_image(self, image: Image.Image, *, tile_size: int = 2048, tile_threshold_pixels: int = 32_000_000) -> None:
        """Display a large PIL image without one pathological giant PhotoImage.

        Tk can become effectively unresponsive when asked to allocate a single
        very large PhotoImage (e.g. Level 5 at 3× zoom). Splitting the same
        raster into independent tiles keeps the canvas responsive while leaving
        scrolling, overlays, and the world coordinate system unchanged.
        """
        self.photo = None
        self.image_item = None
        self.tiled_photos.clear()
        self.tiled_image_items.clear()
        self.chunk_photos.clear()
        self.chunk_image_items.clear()
        self.canvas.delete("all")
        width, height = image.size
        if width * height <= tile_threshold_pixels:
            photo = ImageTk.PhotoImage(image)
            self.photo = photo
            self.image_item = self.canvas.create_image(0, 0, anchor="nw", image=photo, tags=("base_image",))
        else:
            for top in range(0, height, tile_size):
                bottom = min(height, top + tile_size)
                for left in range(0, width, tile_size):
                    right = min(width, left + tile_size)
                    crop = image.crop((left, top, right, bottom))
                    photo = ImageTk.PhotoImage(crop)
                    self.tiled_photos.append(photo)
                    item = self.canvas.create_image(left, top, anchor="nw", image=photo, tags=("base_image",))
                    self.tiled_image_items.append(item)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.rerender_viewport_background()
        self.canvas.tag_raise("base_image")

    def begin_chunked_image(self, width: int, height: int) -> None:
        """Clear the canvas and prepare it for independently replaceable image chunks."""
        self.photo = None
        self.image_item = None
        self.tiled_photos.clear()
        self.tiled_image_items.clear()
        self.chunk_photos.clear()
        self.chunk_image_items.clear()
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.rerender_viewport_background()

    def set_image_chunk(self, key: tuple[int, int], image: Image.Image, x: int, y: int) -> None:
        """Create or replace one chunk PhotoImage at a fixed world-canvas position."""
        photo = ImageTk.PhotoImage(image)
        old_item = self.chunk_image_items.get(key)
        if old_item is not None:
            self.canvas.itemconfigure(old_item, image=photo)
            self.canvas.coords(old_item, x, y)
            item = old_item
        else:
            item = self.canvas.create_image(x, y, anchor="nw", image=photo, tags=("base_image", "level_chunk"))
            self.chunk_image_items[key] = item
        self.chunk_photos[key] = photo
        self.canvas.tag_raise(item)
        # Persistent map chunks must remain below editor overlays and transient ghosts.
        if self.canvas.find_withtag("animated_tile_overlay"):
            self.canvas.tag_lower(item, "animated_tile_overlay")
        elif self.canvas.find_withtag("overlay"):
            self.canvas.tag_lower(item, "overlay")

    def clear(self) -> None:
        self.photo = None
        self.image_item = None
        self.tiled_photos.clear()
        self.tiled_image_items.clear()
        self.chunk_photos.clear()
        self.chunk_image_items.clear()
        self.viewport_background_item = None
        self.viewport_background_resized = None
        self.viewport_background_photo = None
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 0, 0))

class VerticalScrolledFrame(ttk.Frame):
    """A compact vertical scroller for inspector forms."""
    def __init__(self, master: tk.Misc, **kwargs):
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def clear(self, *, preserve_scroll: bool = False) -> tuple[float, float] | None:
        """Clear the inspector body.

        ``VerticalScrolledFrame`` used to force the scroll position back to the
        top every time a staged editor field refreshed itself.  That is hostile
        for the tile/secret inspector, where changing a combobox near the bottom
        may legitimately rebuild a few dependent controls.  Callers that are
        refreshing the *same* logical form can now preserve and restore the
        current y-view explicitly.
        """
        yview = tuple(self.canvas.yview()) if preserve_scroll else None
        for child in self.inner.winfo_children():
            child.destroy()
        if not preserve_scroll:
            self.canvas.yview_moveto(0)
        return yview

    def restore_yview_after_layout(self, yview: tuple[float, float] | None) -> None:
        if not yview:
            return
        self.canvas.after_idle(lambda: self.canvas.yview_moveto(max(0.0, min(1.0, float(yview[0])))))


class Pre2EditorApp(tk.Tk):
    def __init__(self, data_path: Path):
        super().__init__()
        self.title("Prehistorik 2 — Editor MVP")
        self.geometry("1600x980")
        self.minsize(1160, 740)

        self.data_path = data_path
        self.project_dir = Path(__file__).resolve().parents[1]
        self.resources_dir = self.project_dir / "resources"
        self.overlay_settings_path = self.project_dir / "editor_overlay_settings.json"
        self.object_templates_path = self.project_dir / "editor_object_templates.json"
        self._overlay_settings = self._load_overlay_settings()
        self.object_templates = self._load_object_templates()
        self.level = None
        self.union_tiles = b""
        self.front_tiles = b""
        self.background_blob = b""
        self.sprites_blob = b""
        self.sprite_tables = load_sprite_tables(self.resources_dir)
        self.sprite_resolver = SpriteResolver()
        self._level_overlay_photos: list[ImageTk.PhotoImage] = []
        self._animated_tile_overlay_photos: list[ImageTk.PhotoImage] = []
        self.game_file_records: dict[str, dict[str, str]] = {}
        self.parsed_table_rows: dict[str, tuple[str, int]] = {}
        self.selected_parsed_object: tuple[str, int] | None = None
        self.gate_focus_side: dict[int, str] = {}
        self.selected_tile_context: dict[str, object] | None = None
        # Tile behavior edits are staged just like object inspector edits.  The
        # attributes live on the tile definition (all map cells using that tile
        # inherit them), so Apply should be an explicit, undoable boundary.
        self.selected_tile_behavior_draft: dict[str, object] | None = None
        self._selected_tile_behavior_draft_dirty = False
        # Secret mechanics are edited inline with the selected map tile.  A map
        # cell with no active bonus record is represented as a draft with
        # mode="none"; switching to a real secret type allocates or updates a
        # bonus slot only when Apply is pressed.
        self.selected_tile_secret_draft: dict[str, object] | None = None
        self._selected_tile_secret_draft_dirty = False
        self.editor_tool = tk.StringVar(value="View")
        self.object_tool = tk.StringVar(value="Select")
        # Placement preset identity: (kind, source, payload).
        # source = "new", "level", or "user".  Payload is a runtime sprite,
        # a level-table slot, a user-template ID, or None for the default gate.
        self.placement_source = tk.StringVar(value="New")
        self.show_inactive_object_slots = tk.BooleanVar(value=False)
        self.placement_catalog_selection: tuple[str, str, object | None] | None = None
        self.placement_draft: tuple[str, object] | None = None
        self.selected_tile_catalog_num: int | None = None
        self.pending_gate_pick: tuple[int, str] | None = None
        self._object_drag_state: dict[str, object] | None = None
        self.tables_detail_photo: ImageTk.PhotoImage | None = None
        self.tables_detail_tile_photos: list[ImageTk.PhotoImage] = []
        self._detail_vars: list[tk.Variable] = []
        self._tile_property_vars: list[tk.Variable] = []
        # Text typed into ttk.Entry/Spinbox widgets is not guaranteed to trigger
        # <FocusOut> before the Apply button command runs.  Keep the last editor
        # field and its commit callback so Apply can explicitly flush that text.
        self._last_editor_edit_widget: tk.Widget | None = None
        self._editor_widget_committers: dict[tk.Widget, object] = {}
        # Apply must be able to flush hand-typed values without a FocusOut
        # rebuild destroying the Apply button mid-click.
        self._suspend_editor_detail_refresh = False
        # Inspector edits are staged until Apply.  This is especially important
        # for variable-length monster behavior conversions, but it also makes
        # the rest of the object inspector predictable and one-step undoable.
        self.selected_property_draft: tuple[str, int, object] | None = None
        self._selected_property_draft_dirty = False
        self.palettes = load_palettes(self.resources_dir)
        self.current_level_index = 0
        self.dirty = False
        self.undo_stack: list[object] = []
        self.redo_stack: list[object] = []
        self._drag_pre_edit_snapshot = None
        self._tile_paint_state: dict[str, object] | None = None
        self._tile_paint_pre_edit_snapshot = None
        self._tile_paint_preview_photos: dict[tuple[int, int], ImageTk.PhotoImage] = {}
        self._level_chunk_size_tiles = 32
        self._dirty_level_chunks: set[tuple[int, int]] = set()
        self._dirty_level_chunks_after_id: str | None = None
        self._tile_place_ghost_photo: ImageTk.PhotoImage | None = None
        self._tile_place_ghost_item: int | None = None
        self._tile_place_ghost_key: tuple[int, int, int, int] | None = None
        self._object_place_ghost_photo: ImageTk.PhotoImage | None = None
        self._object_place_ghost_items: list[int] = []
        self._object_place_ghost_key: tuple[object, ...] | None = None
        self._tile_catalog_selection_outline: int | None = None

        self.difficulty = tk.StringVar(value="Expert")
        self.zoom = tk.IntVar(value=3)
        self.animation_frame_label = tk.StringVar(value="Frame 1")
        self.animate_tiles = tk.BooleanVar(value=self._overlay_setting_bool("animate_tiles", True))
        self._animation_after_id: str | None = None
        self.grid_enabled = tk.BooleanVar(value=self._overlay_setting_bool("grid_enabled", False))
        self.front_enabled = tk.BooleanVar(value=self._overlay_setting_bool("front_enabled", False))
        self.overlay_front_tiles = tk.BooleanVar(value=self._overlay_setting_bool("overlay_front_tiles", False))
        self.overlay_animated = tk.BooleanVar(value=self._overlay_setting_bool("overlay_animated", False))
        self.overlay_attr1 = tk.BooleanVar(value=self._overlay_setting_bool("overlay_attr1", False))
        self.overlay_attr0 = tk.BooleanVar(value=self._overlay_setting_bool("overlay_attr0", False))
        self.overlay_attr3 = tk.BooleanVar(value=self._overlay_setting_bool("overlay_attr3", False))
        self.overlay_physics_diagram = tk.BooleanVar(value=self._overlay_setting_bool("overlay_physics_diagram", False))

        # Object overlays are drawn from parsed level structures. A few record types
        # have different coordinate domains, handled explicitly in the renderer.
        self.overlay_player_start = tk.BooleanVar(value=self._overlay_setting_bool("overlay_player_start", True))
        self.overlay_monsters = tk.BooleanVar(value=self._overlay_setting_bool("overlay_monsters", False))
        self.overlay_items = tk.BooleanVar(value=self._overlay_setting_bool("overlay_items", False))
        self.overlay_platforms = tk.BooleanVar(value=self._overlay_setting_bool("overlay_platforms", False))
        self.overlay_gates = tk.BooleanVar(value=self._overlay_setting_bool("overlay_gates", False))
        self.overlay_columns = tk.BooleanVar(value=self._overlay_setting_bool("overlay_columns", False))
        self.overlay_bonuses = tk.BooleanVar(value=self._overlay_setting_bool("overlay_bonuses", False))
        self.overlay_boss = tk.BooleanVar(value=self._overlay_setting_bool("overlay_boss", False))
        self._bind_overlay_settings_persistence()

        self.physics_side_var = tk.StringVar(value="solid")
        self.physics_top_var = tk.StringVar(value="solid")
        self.physics_bottom_var = tk.StringVar(value="none")
        self.physics_profile_kind_var = tk.StringVar(value="flat")
        self.physics_profile_base_var = tk.IntVar(value=0)
        self.physics_preset_var = tk.StringVar(value="")

        self.status_text = tk.StringVar(value="Loading...")
        self.tile_info_text = tk.StringVar(value="Tile: —")
        self._level_pan_state: dict[str, int | bool] | None = None
        self._center_on_player_start_pending = True

        self._build_ui()
        self.bind_all("<Control-s>", lambda _event: self._save_current_level())
        self.bind_all("<Control-z>", lambda _event: self._undo())
        self.bind_all("<Control-y>", lambda _event: self._redo())
        self.bind_all("<Delete>", lambda _event: self._delete_selected_object())
        self._load_static_data()
        self._load_level(0)
        self._refresh_game_files()
        self._schedule_tile_animation()

    def _load_overlay_settings(self) -> dict[str, object]:
        try:
            payload = json.loads(self.overlay_settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _load_object_templates(self) -> list[dict[str, object]]:
        """Load user-authored reusable object placement templates.

        Templates are intentionally editor-side metadata.  They store stable
        runtime/global sprite IDs rather than level-local raw sprite numbers so
        a preset can be reused in a different LEVEL*.SQZ.
        """
        try:
            payload = json.loads(self.object_templates_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        out: list[dict[str, object]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            name = row.get("name")
            data = row.get("data")
            template_id = row.get("id")
            if kind not in {"item", "monster", "platform", "gate"}:
                continue
            if not isinstance(name, str) or not name.strip() or not isinstance(data, dict):
                continue
            if not isinstance(template_id, str) or not template_id.strip():
                template_id = f"template-{len(out) + 1}"
            out.append({"id": template_id, "kind": kind, "name": name.strip(), "data": data})
        return out

    def _save_object_templates(self) -> None:
        try:
            self.object_templates_path.write_text(
                json.dumps(self.object_templates, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showwarning("Templates", f"Could not save editor object templates:\n{exc}")

    def _overlay_setting_bool(self, key: str, default: bool) -> bool:
        value = self._overlay_settings.get(key, default)
        return bool(value) if isinstance(value, bool) else default

    def _overlay_setting_vars(self) -> dict[str, tk.BooleanVar]:
        return {
            "animate_tiles": self.animate_tiles,
            "grid_enabled": self.grid_enabled,
            "front_enabled": self.front_enabled,
            "overlay_front_tiles": self.overlay_front_tiles,
            "overlay_animated": self.overlay_animated,
            "overlay_attr1": self.overlay_attr1,
            "overlay_attr0": self.overlay_attr0,
            "overlay_attr3": self.overlay_attr3,
            "overlay_physics_diagram": self.overlay_physics_diagram,
            "overlay_player_start": self.overlay_player_start,
            "overlay_monsters": self.overlay_monsters,
            "overlay_items": self.overlay_items,
            "overlay_platforms": self.overlay_platforms,
            "overlay_gates": self.overlay_gates,
            "overlay_columns": self.overlay_columns,
            "overlay_bonuses": self.overlay_bonuses,
            "overlay_boss": self.overlay_boss,
        }

    def _bind_overlay_settings_persistence(self) -> None:
        for variable in self._overlay_setting_vars().values():
            variable.trace_add("write", self._save_overlay_settings)

    def _save_overlay_settings(self, *_args) -> None:
        payload = {key: bool(var.get()) for key, var in self._overlay_setting_vars().items()}
        self._overlay_settings = payload
        try:
            self.overlay_settings_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            # The editor should remain usable even from a read-only/unwritable folder.
            pass

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(toolbar, text="Game data:").pack(side="left")
        self.path_label = ttk.Label(toolbar, text=str(self.data_path))
        self.path_label.pack(side="left", padx=(6, 14))
        ttk.Button(toolbar, text="Change folder…", command=self._choose_folder).pack(side="left", padx=(0, 18))

        ttk.Label(toolbar, text="Level:").pack(side="left")
        self.level_combo = ttk.Combobox(
            toolbar,
            state="readonly",
            width=18,
            values=[f"Level {name}" for name in LEVEL_IDS],
        )
        self.level_combo.current(0)
        self.level_combo.bind("<<ComboboxSelected>>", self._on_level_changed)
        self.level_combo.pack(side="left", padx=(6, 18))

        ttk.Label(toolbar, text="Difficulty:").pack(side="left")
        self.difficulty_combo = ttk.Combobox(
            toolbar,
            textvariable=self.difficulty,
            state="readonly",
            width=10,
            values=["Beginner", "Expert"],
        )
        self.difficulty_combo.pack(side="left", padx=(6, 18))
        self.difficulty_combo.bind("<<ComboboxSelected>>", self._on_difficulty_changed)

        ttk.Label(toolbar, text="Zoom:").pack(side="left")
        zoom_box = ttk.Combobox(toolbar, state="readonly", width=6, values=["1", "2", "3", "4"])
        zoom_box.set(str(self.zoom.get()))
        zoom_box.bind("<<ComboboxSelected>>", lambda e: self._set_zoom(int(zoom_box.get())))
        zoom_box.pack(side="left", padx=(6, 12))

        tool_group = ttk.Frame(toolbar)
        tool_group.pack(side="left", padx=(0, 12))
        ttk.Label(tool_group, text="Tool:").pack(side="left")
        for tool in ["View", "Select", "Place"]:
            ttk.Radiobutton(
                tool_group,
                text=tool,
                variable=self.editor_tool,
                value=tool,
                command=self._on_editor_tool_changed,
            ).pack(side="left", padx=(6, 0))

        ttk.Button(toolbar, text="Reload", command=self._reload).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Save level", command=self._save_current_level).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Export level…", command=self._export_current_level).pack(side="left", padx=(0, 12))
        ttk.Button(toolbar, text="Undo", command=self._undo).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Redo", command=self._redo).pack(side="left", padx=(0, 12))
        ttk.Button(toolbar, text="Delete selected", command=self._delete_selected_object).pack(side="left")

        self.main_notebook = ttk.Notebook(outer)
        self.main_notebook.pack(fill="both", expand=True)

        self.level_viewer_tab = ttk.Frame(self.main_notebook)
        self.game_file_tab = ttk.Frame(self.main_notebook, padding=8)
        self.main_notebook.add(self.level_viewer_tab, text="Level")
        self.main_notebook.add(self.game_file_tab, text="Files")

        self._build_level_viewer_tab()
        self._build_game_file_tab()

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(8, 0))
        ttk.Label(status, textvariable=self.status_text).pack(side="left", fill="x", expand=True)
        ttk.Label(status, textvariable=self.tile_info_text).pack(side="right")

    def _build_level_viewer_tab(self) -> None:
        pane = ttk.Panedwindow(self.level_viewer_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        viewer_frame = ttk.Frame(pane, padding=(0, 0, 8, 0))
        inspector_frame = ttk.Frame(pane)
        pane.add(viewer_frame, weight=3)
        pane.add(inspector_frame, weight=2)

        self.level_canvas = ScrollableCanvas(viewer_frame, bg="#101010")
        self.level_canvas.pack(fill="both", expand=True)
        self.level_canvas.canvas.bind("<Motion>", self._on_level_motion)
        self.level_canvas.canvas.bind("<ButtonPress-1>", self._on_level_press)
        self.level_canvas.canvas.bind("<Double-Button-1>", self._on_level_double_click)
        self.level_canvas.canvas.bind("<B1-Motion>", self._on_level_drag)
        self.level_canvas.canvas.bind("<ButtonRelease-1>", self._on_level_release)
        self.level_canvas.canvas.bind("<ButtonPress-3>", self._on_level_right_press)
        self.level_canvas.canvas.bind("<B3-Motion>", self._on_level_right_drag)
        self.level_canvas.canvas.bind("<ButtonRelease-3>", self._on_level_right_release)
        self.level_canvas.canvas.bind("<Configure>", lambda _event: self._redraw_animated_tile_overlay(), add="+")

        self.level_side_notebook = ttk.Notebook(inspector_frame)
        self.level_side_notebook.pack(fill="both", expand=True)

        tiles_tab = ttk.Frame(self.level_side_notebook, padding=8)
        objects_tab = ttk.Frame(self.level_side_notebook, padding=8)
        overlays_tab = ttk.Frame(self.level_side_notebook, padding=10)
        info_tab = ttk.Frame(self.level_side_notebook, padding=10)
        self.level_side_notebook.add(tiles_tab, text="Tiles")
        self.level_side_notebook.add(objects_tab, text="Objects")
        self.level_side_notebook.add(overlays_tab, text="Overlays")
        self.level_side_notebook.add(info_tab, text="Info")

        self.tiles_editor_tab = tiles_tab
        self.objects_editor_tab = objects_tab
        self._build_tiles_editor_tab(tiles_tab)
        self._build_objects_editor_tab(objects_tab)
        self._build_overlays_tab(overlays_tab)
        self.info_box = tk.Text(info_tab, width=48, height=35, wrap="word", state="disabled")
        self.info_box.pack(fill="both", expand=True)

    def _build_tiles_editor_tab(self, parent: ttk.Frame) -> None:
        tiles_notebook = ttk.Notebook(parent)
        self.tiles_tool_tabs = tiles_notebook
        tiles_notebook.pack(fill="both", expand=True)
        tile_tab = ttk.Frame(tiles_notebook)
        front_tab = ttk.Frame(tiles_notebook)
        behavior_tab = ttk.Frame(tiles_notebook)
        tiles_notebook.add(tile_tab, text="Tile Catalog")
        tiles_notebook.add(front_tab, text="Front Catalog")
        tiles_notebook.add(behavior_tab, text="Tile Behavior")

        self.tile_canvas = ScrollableCanvas(tile_tab, bg="#202020", width=540, height=760)
        self.tile_canvas.pack(fill="both", expand=True)
        self.tile_canvas.canvas.bind("<Motion>", self._on_tile_browser_motion)
        self.tile_canvas.canvas.bind("<Button-1>", self._on_tile_browser_click)

        self.front_tile_canvas = ScrollableCanvas(front_tab, bg="#202020", width=540, height=760)
        self.front_tile_canvas.pack(fill="both", expand=True)
        self.front_tile_canvas.canvas.bind("<Motion>", self._on_front_tile_browser_motion)
        self.front_tile_canvas.canvas.bind("<Button-1>", self._on_front_tile_browser_click)

        self._build_physics_lab_tab(behavior_tab)

    def _build_objects_editor_tab(self, parent: ttk.Frame) -> None:
        self._build_tables_tab(parent)

    def _build_overlays_tab(self, parent: ttk.Frame) -> None:
        render_group = ttk.LabelFrame(parent, text="Rendered layers", padding=10)
        render_group.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(
            render_group,
            text="Front tile layer",
            variable=self.front_enabled,
            command=self._rerender_level,
        ).pack(anchor="w")
        ttk.Checkbutton(
            render_group,
            text="Tile grid",
            variable=self.grid_enabled,
            command=self._redraw_level_overlays,
        ).pack(anchor="w")

        tile_group = ttk.LabelFrame(parent, text="Tile overlays", padding=10)
        tile_group.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(
            tile_group,
            text="Front-layer tile flags",
            variable=self.overlay_front_tiles,
            command=self._redraw_level_overlays,
        ).pack(anchor="w")
        ttk.Checkbutton(
            tile_group,
            text="Animate tile groups",
            variable=self.animate_tiles,
            command=self._on_animate_tiles_changed,
        ).pack(anchor="w")
        ttk.Checkbutton(
            tile_group,
            text="Animated tile markers",
            variable=self.overlay_animated,
            command=self._redraw_level_overlays,
        ).pack(anchor="w")
        ttk.Checkbutton(
            tile_group,
            text="Physics diagram: sides / surfaces / slopes",
            variable=self.overlay_physics_diagram,
            command=self._redraw_level_overlays,
        ).pack(anchor="w")

        objects_group = ttk.LabelFrame(parent, text="Object overlays", padding=10)
        objects_group.pack(fill="x", pady=(0, 10))
        object_rows = [
            ("Player start", self.overlay_player_start),
            ("Enemies / monsters", self.overlay_monsters),
            ("Items", self.overlay_items),
            ("Platforms", self.overlay_platforms),
            ("Gates", self.overlay_gates),
            ("Shifting columns", self.overlay_columns),
            ("Secret markers", self.overlay_bonuses),
            ("Boss", self.overlay_boss),
        ]
        for label, var in object_rows:
            ttk.Checkbutton(objects_group, text=label, variable=var, command=self._redraw_level_overlays).pack(anchor="w")

        behavior_legend = ttk.LabelFrame(parent, text="Physics color language", padding=10)
        behavior_legend.pack(fill="x", pady=(0, 10))
        ttk.Label(behavior_legend, text="Green — solid / supportive collision").pack(anchor="w")
        ttk.Label(behavior_legend, text="Cyan — slippery / icy top surface").pack(anchor="w")
        ttk.Label(behavior_legend, text="Yellow — drop-through top surface").pack(anchor="w")
        ttk.Label(behavior_legend, text="Red — deadly contact").pack(anchor="w")

        secrets_legend = ttk.LabelFrame(parent, text="Secret colors", padding=10)
        secrets_legend.pack(fill="x", pady=(0, 10))
        ttk.Label(secrets_legend, text="Yellow — small random bonus drop").pack(anchor="w")
        ttk.Label(secrets_legend, text="Blue — tile reveal / appearing structure").pack(anchor="w")
        ttk.Label(secrets_legend, text="Red — big random bonus").pack(anchor="w")

        note = ttk.Label(
            parent,
            text=(
                "Types 0 and 10 in the monster table are drawn as activation regions, "
                "not fake fixed spawn points; other monsters use pixel coordinates from the level data."
            ),
            wraplength=430,
            justify="left",
        )
        note.pack(fill="x", pady=(2, 0))

    def _build_tables_tab(self, tables_tab: ttk.Frame) -> None:
        pane = ttk.Panedwindow(tables_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        tool_frame = ttk.Frame(pane, padding=(0, 0, 8, 0))
        detail_frame = ttk.Frame(pane)
        pane.add(tool_frame, weight=1)
        pane.add(detail_frame, weight=3)

        self.object_tool_tabs = ttk.Notebook(tool_frame)
        self.object_tool_tabs.pack(fill="both", expand=True)
        select_frame = ttk.Frame(self.object_tool_tabs, padding=(0, 0, 0, 0))
        place_frame = ttk.Frame(self.object_tool_tabs, padding=(0, 0, 0, 0))
        self.object_tool_tabs.add(select_frame, text="Select")
        self.object_tool_tabs.add(place_frame, text="Place")
        self.object_tool_tabs.bind("<<NotebookTabChanged>>", self._on_object_tool_tab_changed)

        self.table_choice = tk.StringVar(value="Monsters")
        self.object_group_tabs = ttk.Notebook(select_frame)
        self.object_group_tabs.pack(fill="x", pady=(0, 8))
        for group in ["Monsters", "Items", "Platforms", "Gates", "Columns", "Boss"]:
            self.object_group_tabs.add(ttk.Frame(self.object_group_tabs), text=group)
        self.object_group_tabs.bind("<<NotebookTabChanged>>", self._on_object_group_tab_changed)

        ttk.Label(
            select_frame,
            text="Instances already placed in this level. Single-click inspects; double-click jumps the camera to that object.",
            wraplength=240,
            justify="left",
        ).pack(fill="x", pady=(0, 4))
        ttk.Checkbutton(
            select_frame,
            text="Show inactive slots",
            variable=self.show_inactive_object_slots,
            command=self._refresh_tables,
        ).pack(anchor="w", fill="x", pady=(0, 8))

        tree_frame = ttk.Frame(select_frame)
        tree_frame.pack(fill="both", expand=True)
        self.tables_tree = ttk.Treeview(tree_frame, show="headings", height=30, selectmode="browse")
        self.tables_tree.pack(side="left", fill="both", expand=True)
        self.tables_tree.bind("<<TreeviewSelect>>", self._on_parsed_table_selected)
        self.tables_tree.bind("<ButtonRelease-1>", self._on_parsed_table_click, add="+")
        self.tables_tree.bind("<Double-1>", self._on_parsed_table_double_click, add="+")
        self.tables_yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tables_tree.yview)
        self.tables_yscroll.pack(side="right", fill="y")
        self.tables_tree.configure(yscrollcommand=self.tables_yscroll.set)

        ttk.Label(
            place_frame,
            text=(
                "Place a new authored object or reuse a deduplicated template. "
                "The right-hand draft inspector remains editable before every map click."
            ),
            wraplength=240,
            justify="left",
        ).pack(fill="x", pady=(0, 8))

        source_frame = ttk.LabelFrame(place_frame, text="Placement source", padding=6)
        source_frame.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(
            source_frame,
            text="New",
            value="New",
            variable=self.placement_source,
            command=self._on_placement_source_changed,
        ).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(
            source_frame,
            text="Templates",
            value="Templates",
            variable=self.placement_source,
            command=self._on_placement_source_changed,
        ).pack(side="left")

        template_actions = ttk.Frame(place_frame)
        template_actions.pack(fill="x", pady=(0, 8))
        self.save_draft_template_button = ttk.Button(
            template_actions,
            text="Save draft as template…",
            command=self._save_current_draft_as_template,
        )
        self.save_draft_template_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.rename_template_button = ttk.Button(
            template_actions,
            text="Rename saved…",
            command=self._rename_selected_user_template,
        )
        self.rename_template_button.pack(side="left", padx=(0, 4))
        self.delete_template_button = ttk.Button(
            template_actions,
            text="Delete saved",
            command=self._delete_selected_user_template,
        )
        self.delete_template_button.pack(side="left")

        self.placement_catalog_tabs = ttk.Notebook(place_frame)
        self.placement_catalog_tabs.pack(fill="x", pady=(0, 8))
        for group in ["Items", "Monsters", "Platforms", "Gates"]:
            self.placement_catalog_tabs.add(ttk.Frame(self.placement_catalog_tabs), text=group)
        self.placement_catalog_tabs.bind("<<NotebookTabChanged>>", self._on_placement_catalog_tab_changed)
        self.object_catalog_tree = ttk.Treeview(place_frame, columns=["type"], show="headings", height=16, selectmode="browse")
        self.object_catalog_tree.heading("type", text="Type / template")
        self.object_catalog_tree.column("type", width=240, anchor="w", stretch=True)
        self.object_catalog_tree.pack(fill="both", expand=True)
        self.object_catalog_tree.bind("<<TreeviewSelect>>", self._on_object_catalog_selected)

        detail_header = ttk.LabelFrame(detail_frame, text="Selected object", padding=8)
        detail_header.pack(fill="both", expand=True)

        self.tables_detail_canvas = tk.Canvas(
            detail_header,
            width=220,
            height=180,
            bg="#202020",
            highlightthickness=0,
        )
        self.tables_detail_canvas.pack(fill="x", pady=(0, 8))

        self.tables_detail_controls = ttk.LabelFrame(
            detail_header,
            text="Properties",
            padding=6,
        )
        self.tables_detail_controls.pack(fill="both", expand=True, pady=(0, 8))
        self.tables_detail_form = VerticalScrolledFrame(self.tables_detail_controls)
        self.tables_detail_form.pack(fill="both", expand=True)
        self._set_parsed_detail_text(
            "Select an object on the left to inspect the mechanics fields that are understood so far. Tile-bound secrets live in the Tiles editor."
        )

    def _active_level_editor_tab(self) -> str:
        if not hasattr(self, "level_side_notebook"):
            return ""
        tab_id = self.level_side_notebook.select()
        return str(self.level_side_notebook.tab(tab_id, "text")) if tab_id else ""

    def _on_editor_tool_changed(self) -> None:
        tool = self.editor_tool.get()
        self._clear_tile_place_ghost()
        self._clear_object_place_ghost()
        if tool != "Select":
            self.pending_gate_pick = None
        if tool == "Place" and self._active_level_editor_tab() == "Objects" and hasattr(self, "object_tool_tabs"):
            self.object_tool_tabs.select(1)
            self._refresh_placement_draft_detail()
        elif tool in {"View", "Select"} and self._active_level_editor_tab() == "Objects" and hasattr(self, "object_tool_tabs"):
            self.object_tool_tabs.select(0)
            if self.selected_parsed_object is not None:
                self._refresh_parsed_detail(*self.selected_parsed_object)
        self.status_text.set(f"{tool} tool active in {self._active_level_editor_tab() or 'Level'} editor.")

    def _on_object_tool_tab_changed(self, _event=None) -> None:
        if not hasattr(self, "object_tool_tabs"):
            return
        tab_id = self.object_tool_tabs.select()
        if not tab_id:
            return
        tab_name = self.object_tool_tabs.tab(tab_id, "text")
        if tab_name == "Select":
            self.object_tool.set("Select")
            if self.editor_tool.get() == "Place":
                self.editor_tool.set("Select")
            if self.selected_parsed_object is not None:
                self._refresh_parsed_detail(*self.selected_parsed_object)
        else:
            self.editor_tool.set("Place")
            if self.placement_catalog_selection is not None:
                kind = self.placement_catalog_selection[0]
                self.object_tool.set({"item": "Place item", "monster": "Place monster", "platform": "Place platform", "gate": "Place gate"}.get(kind, "Select"))
            else:
                self.object_tool.set("Select")
            self._refresh_placement_draft_detail()
        if tab_name != "Select" and self.placement_catalog_selection is not None:
            kind = self.placement_catalog_selection[0]
            self.object_tool.set({"item": "Place item", "monster": "Place monster", "platform": "Place platform", "gate": "Place gate"}.get(kind, "Select"))

    def _on_object_group_tab_changed(self, _event=None) -> None:
        if not hasattr(self, "object_group_tabs"):
            return
        tab_id = self.object_group_tabs.select()
        if not tab_id:
            return
        self.table_choice.set(self.object_group_tabs.tab(tab_id, "text"))
        self._refresh_tables()

    def _select_object_group_tab(self, group: str) -> None:
        if not hasattr(self, "object_group_tabs"):
            return
        for tab_id in self.object_group_tabs.tabs():
            if self.object_group_tabs.tab(tab_id, "text") == group:
                self.object_group_tabs.select(tab_id)
                return

    def _selected_placement_group(self) -> str:
        if not hasattr(self, "placement_catalog_tabs"):
            return "Items"
        tab_id = self.placement_catalog_tabs.select()
        if not tab_id:
            return "Items"
        return str(self.placement_catalog_tabs.tab(tab_id, "text"))

    def _on_placement_source_changed(self) -> None:
        self._refresh_object_catalog()
        self._clear_object_place_ghost()
        source = self.placement_source.get()
        self.status_text.set(f"Objects placement source: {source}.")

    def _on_placement_catalog_tab_changed(self, _event=None) -> None:
        self._refresh_object_catalog()

    def _placement_tab_kind(self) -> str:
        return {
            "Items": "item",
            "Monsters": "monster",
            "Platforms": "platform",
            "Gates": "gate",
        }.get(self._selected_placement_group(), "item")

    def _set_placement_tool_for_kind(self, kind: str) -> None:
        self.object_tool.set({
            "item": "Place item",
            "monster": "Place monster",
            "platform": "Place platform",
            "gate": "Place gate",
        }.get(kind, "Select"))
        self.editor_tool.set("Place")

    def _on_object_catalog_selected(self, _event=None) -> None:
        selection = self.object_catalog_tree.selection()
        if not selection:
            self.placement_catalog_selection = None
            self.placement_draft = None
            self._refresh_placement_draft_detail()
            return
        iid = str(selection[0])
        parts = iid.split(":", 2)
        if len(parts) != 3:
            return
        source, kind, raw_payload = parts
        if source not in {"new", "level", "user"} or kind not in {"item", "monster", "platform", "gate"}:
            return
        payload: object | None
        if source == "new":
            payload = None if kind == "gate" else int(raw_payload)
        elif source == "level":
            payload = int(raw_payload)
        else:
            payload = raw_payload
        self.placement_catalog_selection = (kind, source, payload)
        self._set_placement_tool_for_kind(kind)
        self._build_placement_draft()
        self._refresh_placement_draft_detail()
        self._clear_object_place_ghost()
        if hasattr(self, "object_tool_tabs"):
            self.object_tool_tabs.select(1)

    def _monster_template_key(self, monster) -> tuple[object, ...]:
        if self.level is None:
            runtime_sprite = monster.sprite_num_raw
        else:
            runtime_sprite = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
        if monster.uses_trigger_rect:
            _tx, _ty, tw, th = monster.trigger_rect_tiles or (0, 0, 0, 0)
            placement_shape: tuple[object, ...] = ("trigger", tw, th)
        else:
            placement_shape = ("fixed",)
        extra = tuple(sorted((str(key), repr(value)) for key, value in monster.extra.items()))
        return (
            runtime_sprite,
            monster.type_byte,
            monster.flags,
            monster.energy,
            monster.respawn_ticks,
            monster.current_tick,
            monster.score,
            placement_shape,
            extra,
        )

    def _platform_template_key(self, platform) -> tuple[object, ...]:
        if self.level is None:
            runtime_sprite = platform.sprite_num_raw
        else:
            runtime_sprite = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
        extra = tuple(sorted((str(key), repr(value)) for key, value in platform.extra.items()))
        return runtime_sprite, platform.flags, platform.variant, extra

    def _item_template_key(self, item) -> tuple[object, ...]:
        if self.level is None:
            runtime_sprite = item.sprite_num_raw
        else:
            runtime_sprite = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
        return runtime_sprite, item.y_delta

    def _template_label(self, kind: str, draft, *, prefix: str = "") -> str:
        if self.level is None:
            return f"{prefix}{kind.title()} template"
        if kind == "item":
            sprite_view = self.sprite_resolver.item_sprite(self.level, draft.sprite_num_raw)
            tuning = f" · yΔ {draft.y_delta}" if getattr(draft, "y_delta", 0) else ""
            return f"{prefix}{item_visual_name(sprite_view)}{tuning}"
        if kind == "monster":
            sprite_view = self.sprite_resolver.monster_sprite(self.level, draft.sprite_num_raw)
            return f"{prefix}{monster_display_name(sprite_view, draft.behavior_name, draft.movement_type)}"
        if kind == "platform":
            sprite_view = self.sprite_resolver.platform_sprite(self.level, draft.sprite_num_raw)
            return f"{prefix}{platform_display_name(sprite_view, draft.platform_type)}"
        if kind == "gate":
            return f"{prefix}Gate / teleporter link · scroll {draft.scroll_flag}"
        return f"{prefix}{kind.title()} template"

    def _new_catalog_rows(self, kind: str) -> list[tuple[str, str]]:
        if kind == "item":
            return [(f"new:item:{runtime}", item_visual_name(runtime)) for runtime in sorted(ITEM_VISUAL_NAMES)]
        if kind == "monster":
            return [
                (
                    f"new:monster:{runtime}",
                    monster_visual_name(runtime, MONSTER_PLACEMENT_DEFAULT_BEHAVIOR.get(runtime, 9)),
                )
                for runtime in MONSTER_PLACEMENT_VISUALS
            ]
        if kind == "platform":
            return [(f"new:platform:{runtime}", platform_visual_name(runtime)) for runtime in sorted(PLATFORM_VISUAL_NAMES)]
        if kind == "gate":
            return [("new:gate:default", "Gate / teleporter link")]
        return []

    def _level_template_rows(self, kind: str) -> list[tuple[str, str]]:
        if self.level is None:
            return []
        rows: list[tuple[str, str]] = []
        if kind == "item":
            seen: set[tuple[object, ...]] = set()
            for idx, item in enumerate(self.level.items):
                if not item.active:
                    continue
                key = self._item_template_key(item)
                if key in seen:
                    continue
                seen.add(key)
                rows.append((f"level:item:{idx}", self._template_label("item", item, prefix="Level — ")))
        elif kind == "monster":
            seen = set()
            for idx, monster in enumerate(self.level.monsters):
                if self.difficulty.get() == "Beginner" and monster.expert_only:
                    continue
                key = self._monster_template_key(monster)
                if key in seen:
                    continue
                seen.add(key)
                rows.append((f"level:monster:{idx}", self._template_label("monster", monster, prefix="Level — ")))
        elif kind == "platform":
            seen = set()
            for idx, platform in enumerate(self.level.platforms):
                if not platform.active:
                    continue
                key = self._platform_template_key(platform)
                if key in seen:
                    continue
                seen.add(key)
                rows.append((f"level:platform:{idx}", self._template_label("platform", platform, prefix="Level — ")))
        elif kind == "gate":
            seen: set[int] = set()
            for idx, gate in enumerate(self.level.gates):
                if not gate.active or gate.scroll_flag in seen:
                    continue
                seen.add(gate.scroll_flag)
                rows.append((f"level:gate:{idx}", self._template_label("gate", gate, prefix="Level — ")))
        return rows

    def _user_template_rows(self, kind: str) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for template in self.object_templates:
            if template.get("kind") != kind:
                continue
            template_id = str(template.get("id", ""))
            name = str(template.get("name", "Saved template"))
            rows.append((f"user:{kind}:{template_id}", f"Saved — {name}"))
        return rows

    def _refresh_object_catalog(self) -> None:
        if not hasattr(self, "object_catalog_tree"):
            return
        self.object_catalog_tree.delete(*self.object_catalog_tree.get_children())
        self.placement_catalog_selection = None
        self.placement_draft = None
        if self.level is None:
            if hasattr(self, "tables_detail_form") and self.editor_tool.get() == "Place":
                self._refresh_placement_draft_detail()
            return
        kind = self._placement_tab_kind()
        if self.placement_source.get() == "Templates":
            rows = self._level_template_rows(kind) + self._user_template_rows(kind)
        else:
            rows = self._new_catalog_rows(kind)
        for iid, label in rows:
            self.object_catalog_tree.insert("", "end", iid=iid, values=(label,))
        if hasattr(self, "delete_template_button"):
            state = "normal" if self.placement_source.get() == "Templates" else "disabled"
            self.delete_template_button.configure(state=state)
            if hasattr(self, "rename_template_button"):
                self.rename_template_button.configure(state=state)
        if not rows and hasattr(self, "tables_detail_form") and self.editor_tool.get() == "Place":
            self._refresh_placement_draft_detail()

    def _build_physics_lab_tab(self, parent: ttk.Frame) -> None:
        intro = ttk.Label(
            parent,
            text=(
                "Tile Editor MVP: click a tile in the Level tab to inspect its visual tile, physics, animation flags, "
                "and any secret mechanic attached to that map cell."
            ),
            wraplength=430,
            justify="left",
        )
        intro.pack(fill="x", pady=(0, 8))

        selected_frame = ttk.LabelFrame(parent, text="Selected map tile", padding=8)
        selected_frame.pack(fill="x", pady=(0, 8))
        self.tile_properties_title = tk.StringVar(value="No map tile selected yet")
        ttk.Label(
            selected_frame,
            textvariable=self.tile_properties_title,
            wraplength=430,
            justify="left",
        ).pack(fill="x")

        preview_frame = ttk.LabelFrame(parent, text="Tile behavior preview", padding=8)
        preview_frame.pack(fill="x", pady=(0, 8))
        self.physics_lab_canvas = tk.Canvas(preview_frame, width=240, height=240, bg="#202020", highlightthickness=0)
        self.physics_lab_canvas.pack(fill="x", expand=False)

        self.tile_properties_form_container = ttk.LabelFrame(parent, text="Tile + secret properties", padding=6)
        self.tile_properties_form_container.pack(fill="both", expand=True)
        self.tile_properties_form = VerticalScrolledFrame(self.tile_properties_form_container)
        self.tile_properties_form.pack(fill="both", expand=True)

        # One shared, always-visible commit bar for the whole Tile + Secret form.
        # Collision/surface fields and map-cell secret mechanics are part of the
        # same editing task, so exposing two unrelated Apply/Discard rows was
        # confusing and made it too easy to click the wrong one after a refresh.
        self.tile_properties_action_bar = ttk.LabelFrame(
            self.tile_properties_form_container,
            text="Tile + secret staged edits",
            padding=6,
        )
        self.tile_properties_action_bar.pack(fill="x", pady=(6, 0))
        self.tile_properties_action_status = tk.StringVar(
            value="Select a map tile to edit its tile definition and secret mechanic."
        )
        ttk.Label(
            self.tile_properties_action_bar,
            textvariable=self.tile_properties_action_status,
            wraplength=430,
            justify="left",
        ).pack(fill="x", anchor="w", pady=(0, 5))
        tile_action_buttons = ttk.Frame(self.tile_properties_action_bar)
        tile_action_buttons.pack(fill="x")
        self.tile_properties_apply_button = ttk.Button(
            tile_action_buttons,
            text="Apply",
            command=self._apply_tile_properties_drafts,
        )
        self.tile_properties_apply_button.bind(
            "<ButtonPress-1>",
            lambda _event: self._commit_all_editor_edit_widgets(),
            add="+",
        )
        self.tile_properties_apply_button.pack(side="left", fill="x", expand=True)
        self.tile_properties_discard_button = ttk.Button(
            tile_action_buttons,
            text="Discard",
            command=self._discard_tile_properties_drafts,
        )
        self.tile_properties_discard_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._update_tile_properties_action_bar()
        self._refresh_tile_properties_form(None)

    def _build_game_file_tab(self) -> None:
        pane = ttk.Panedwindow(self.game_file_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = ttk.Frame(pane, padding=(0, 0, 8, 0))
        right = ttk.Frame(pane)
        pane.add(left, weight=2)
        pane.add(right, weight=3)

        header = ttk.Frame(left)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Game files").pack(side="left")
        ttk.Button(header, text="Refresh", command=self._refresh_game_files).pack(side="right")

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)
        columns = ["file", "size", "decoded", "parser"]
        self.files_tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        for col in columns:
            self.files_tree.heading(col, text=col.title())
        self.files_tree.column("file", width=220, anchor="w", stretch=True)
        self.files_tree.column("size", width=100, anchor="e", stretch=False)
        self.files_tree.column("decoded", width=100, anchor="e", stretch=False)
        self.files_tree.column("parser", width=220, anchor="w", stretch=True)
        self.files_tree.pack(side="left", fill="both", expand=True)
        self.files_tree.bind("<<TreeviewSelect>>", self._on_game_file_selected)

        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.files_tree.yview)
        yscroll.pack(side="right", fill="y")
        self.files_tree.configure(yscrollcommand=yscroll.set)

        self.file_preview_notebook = ttk.Notebook(right)
        self.file_preview_notebook.pack(fill="both", expand=True)

        summary_tab = ttk.Frame(self.file_preview_notebook, padding=8)
        visual_tab = ttk.Frame(self.file_preview_notebook)
        self.file_preview_notebook.add(summary_tab, text="File Info")
        self.file_preview_notebook.add(visual_tab, text="Visual Preview")

        self.file_preview_text = tk.Text(summary_tab, wrap="word", state="disabled")
        self.file_preview_text.pack(fill="both", expand=True)

        self.file_preview_canvas = ScrollableCanvas(visual_tab, bg="#202020")
        self.file_preview_canvas.pack(fill="both", expand=True)

    def _choose_folder(self) -> None:
        if not self._confirm_discard_or_save_changes():
            return
        selected = filedialog.askdirectory(initialdir=str(self.data_path), title="Choose Prehistorik 2 data folder")
        if not selected:
            return
        self.data_path = Path(selected)
        self.path_label.configure(text=str(self.data_path))
        self._load_static_data()
        self._load_level(self.current_level_index)
        self._refresh_game_files()

    def _level_file_path(self) -> Path | None:
        if self.level is None:
            return None
        return self.data_path / f"level{self.level.level_id.lower()}.sqz"

    def _dirty_prefix(self) -> str:
        return "* " if self.dirty else ""

    def _mark_dirty(self, message: str | None = None) -> None:
        self.dirty = True
        if message:
            self.status_text.set(f"{self._dirty_prefix()}{message}")

    def _record_undo_state(self) -> None:
        if self.level is None:
            return
        self.undo_stack.append(copy.deepcopy(self.level))
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _restore_level_snapshot(self, snapshot) -> None:
        self.level = copy.deepcopy(snapshot)
        self.dirty = True
        self._rerender_all()
        self.status_text.set(f"{self._dirty_prefix()}Editor state restored.")

    def _undo(self) -> None:
        if self.level is None or not self.undo_stack:
            self.status_text.set("Nothing to undo.")
            return
        self.redo_stack.append(copy.deepcopy(self.level))
        snapshot = self.undo_stack.pop()
        self._restore_level_snapshot(snapshot)

    def _redo(self) -> None:
        if self.level is None or not self.redo_stack:
            self.status_text.set("Nothing to redo.")
            return
        self.undo_stack.append(copy.deepcopy(self.level))
        snapshot = self.redo_stack.pop()
        self._restore_level_snapshot(snapshot)

    def _confirm_discard_or_save_changes(self) -> bool:
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved level changes",
            "This level has unsaved edits. Save it before continuing?",
        )
        if answer is None:
            return False
        if answer:
            return self._save_current_level(show_dialog_on_success=False)
        return True

    def _save_current_level(self, *, show_dialog_on_success: bool = True) -> bool:
        if self.level is None:
            self.status_text.set("No level loaded.")
            return False
        path = self._level_file_path()
        if path is None:
            return False
        try:
            backup = path.with_suffix(path.suffix + ".bak")
            if path.exists() and not backup.exists():
                backup.write_bytes(path.read_bytes())
            raw = serialize_level(self.level)
            save_level_sqz(path, self.level)
            self.level.raw = raw
            self.dirty = False
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._refresh_game_files()
            message = f"Saved {path.name}. First overwrite keeps {backup.name} as a backup."
            self.status_text.set(message)
            if show_dialog_on_success:
                messagebox.showinfo("Level saved", message)
            return True
        except Exception as exc:
            messagebox.showerror("Unable to save level", str(exc))
            self.status_text.set(f"Save failed: {exc}")
            return False

    def _export_current_level(self) -> None:
        if self.level is None:
            self.status_text.set("No level loaded.")
            return
        initial = f"level{self.level.level_id.lower()}_edited.sqz"
        filename = filedialog.asksaveasfilename(
            title="Export edited LEVEL*.SQZ",
            initialdir=str(self.data_path),
            initialfile=initial,
            defaultextension=".sqz",
            filetypes=[("Prehistorik 2 SQZ", "*.sqz"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            save_level_sqz(filename, self.level)
            self.status_text.set(f"Exported edited level to {Path(filename).name}.")
        except Exception as exc:
            messagebox.showerror("Unable to export level", str(exc))
            self.status_text.set(f"Export failed: {exc}")

    def _delete_selected_object(self) -> None:
        if self.level is None or self.selected_parsed_object is None:
            self.status_text.set("Select an object in the Objects editor first.")
            return
        kind, index = self.selected_parsed_object
        self._record_undo_state()
        deleted = False
        if kind == "monster" and 0 <= index < len(self.level.monsters):
            self.level.monsters.pop(index)
            deleted = True
        elif kind == "item" and 0 <= index < len(self.level.items):
            item = self.level.items[index]
            item.sprite_num_raw = 0xFFFF
            item.x_pos = 0
            item.y_pos = 0
            item.y_delta = 0
            deleted = True
        elif kind == "platform" and 0 <= index < len(self.level.platforms):
            platform = self.level.platforms[index]
            platform.sprite_num_raw = 0xFFFF
            platform.x_pos = 0
            platform.y_pos = 0
            deleted = True
        elif kind == "gate" and 0 <= index < len(self.level.gates):
            gate = self.level.gates[index]
            gate.enter_pos = 0xFFFF
            gate.tilemap_pos = 0xFFFF
            gate.dst_pos = 0xFFFF
            gate.scroll_flag = 0
            deleted = True
        elif kind == "column" and 0 <= index < len(self.level.columns):
            column = self.level.columns[index]
            column.tilemap_pos = 0xFFFF
            column.trigger_pos = 0xFFFF
            column.width = 0
            column.height = 0
            deleted = True
        elif kind == "secret" and 0 <= index < len(self.level.bonuses):
            self.level.bonuses[index].pos = 0xFFFF
            deleted = True
        elif kind == "boss":
            self.level.boss.state = 0xFF
            deleted = True
        if not deleted:
            if self.undo_stack:
                self.undo_stack.pop()
            self.status_text.set(f"Delete is not supported for {kind}.")
            return
        self.selected_parsed_object = None
        self._refresh_tables()
        self._redraw_level_overlays()
        self._refresh_info()
        self._mark_dirty(f"Deleted {kind} {index}.")

    def _load_static_data(self) -> None:
        try:
            self.union_tiles = load_union_tiles(self.data_path)
            self.front_tiles = load_front_tiles(self.data_path)
        except Exception as exc:
            messagebox.showerror("Unable to load shared tile resources", str(exc))
            self.union_tiles = b""
            self.front_tiles = b""
        try:
            self.sprites_blob = load_sprites_blob(self.data_path)
        except Exception as exc:
            self.sprites_blob = b""
            self.status_text.set(f"SPRITES.SQZ load failed: {exc}")

    def _load_level(self, index: int) -> None:
        try:
            self.level = load_level(self.data_path, index)
            self.current_level_index = index
            self.dirty = False
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._drag_pre_edit_snapshot = None
            self._tile_paint_state = None
            self._tile_paint_pre_edit_snapshot = None
            self.placement_catalog_selection = None
            self.placement_draft = None
            try:
                self.background_blob = load_background_bitmap(self.data_path, index)
            except Exception as exc:
                self.background_blob = b""
                self.status_text.set(f"Background load failed for Level {self.level.level_id}: {exc}")
            self._center_on_player_start_pending = True
            self._rerender_all()
        except Exception as exc:
            self.level = None
            self.level_canvas.clear()
            self.tile_canvas.clear()
            self.front_tile_canvas.clear()
            self._set_info_text(f"Failed to load level:\n\n{exc}")
            self.status_text.set(f"Load failed: {exc}")

    def _reload(self) -> None:
        if not self._confirm_discard_or_save_changes():
            return
        self._load_static_data()
        self._load_level(self.current_level_index)
        self._refresh_game_files()

    def _on_level_changed(self, _event=None) -> None:
        requested = self.level_combo.current()
        if requested == self.current_level_index:
            return
        if not self._confirm_discard_or_save_changes():
            self.level_combo.current(self.current_level_index)
            return
        self._load_level(requested)

    def _on_difficulty_changed(self, _event=None) -> None:
        self._redraw_level_overlays()
        self._refresh_info()
        self._refresh_tables()
        if getattr(self, "placement_source", None) is not None and self.placement_source.get() == "Templates":
            self._refresh_object_catalog()

    def _visible_monsters(self):
        if self.level is None:
            return []
        expert = self.difficulty.get() == "Expert"
        return [monster for monster in self.level.monsters if expert or not monster.expert_only]

    def _set_zoom(self, zoom: int) -> None:
        self.zoom.set(max(1, min(4, zoom)))
        self._rerender_level()

    def _animation_frame_index(self) -> int:
        if not self.animate_tiles.get():
            return 0
        return {"Frame 1": 0, "Frame 2": 1, "Frame 3": 2}.get(self.animation_frame_label.get(), 0)

    def _on_animation_frame_changed(self, _event=None) -> None:
        self._redraw_animated_tile_overlay()

    def _on_animate_tiles_changed(self) -> None:
        if self.animate_tiles.get():
            self._schedule_tile_animation()
        else:
            if self._animation_after_id is not None:
                self.after_cancel(self._animation_after_id)
                self._animation_after_id = None
            self.animation_frame_label.set("Frame 1")
        self._on_animation_frame_changed()
        if self.selected_tile_context is not None:
            self._refresh_tile_properties_form(self.selected_tile_context)
        self._refresh_info()

    def _schedule_tile_animation(self) -> None:
        if self._animation_after_id is None and self.animate_tiles.get():
            self._animation_after_id = self.after(300, self._advance_tile_animation)

    def _advance_tile_animation(self) -> None:
        self._animation_after_id = None
        if self.level is None or not self.animate_tiles.get():
            return
        frames = ["Frame 1", "Frame 2", "Frame 3"]
        current = frames.index(self.animation_frame_label.get()) if self.animation_frame_label.get() in frames else 0
        self.animation_frame_label.set(frames[(current + 1) % len(frames)])
        self._on_animation_frame_changed()
        self._schedule_tile_animation()

    def _physics_side_value(self) -> int:
        return {"none": 0, "solid": 1, "deadly": 2}.get(self.physics_side_var.get(), 0)

    def _physics_top_value(self) -> int:
        return {
            "none": 0,
            "solid": 1,
            "slightly_slippery": 2,
            "slippery": 3,
            "very_slippery": 4,
            "drop_through": 5,
            "deadly": 6,
        }.get(self.physics_top_var.get(), 0)

    def _physics_bottom_value(self) -> int:
        return {"none": 0, "solid": 1, "deadly": 2}.get(self.physics_bottom_var.get(), 0)

    def _physics_attr3_value(self) -> int:
        try:
            base = max(0, min(15, int(self.physics_profile_base_var.get())))
        except Exception:
            base = 0
            self.physics_profile_base_var.set(0)
        slope_bits = {
            "flat": 0x00,
            "slope_down_right": 0x10,
            "slope_up_right": 0x20,
        }.get(self.physics_profile_kind_var.get(), 0x00)
        return slope_bits | base

    def _load_physics_lab_values(self, attr0: int, attr1: int, attr2: int, attr3: int) -> None:
        self.physics_side_var.set({0: "none", 1: "solid", 2: "deadly"}.get(attr0, "none"))
        self.physics_top_var.set({
            0: "none",
            1: "solid",
            2: "slightly_slippery",
            3: "slippery",
            4: "very_slippery",
            5: "drop_through",
            6: "deadly",
        }.get(attr1, "none"))
        self.physics_bottom_var.set({0: "none", 1: "solid", 2: "deadly"}.get(attr2 & 0x0F, "none"))
        self.physics_profile_kind_var.set({0x00: "flat", 0x10: "slope_down_right", 0x20: "slope_up_right"}.get(attr3 & 0x30, "flat"))
        self.physics_profile_base_var.set(attr3 & 0x0F)
        self._redraw_physics_lab()

    def _set_physics_lab_text(self, text: str) -> None:
        self._tile_physics_summary_cache = text

    def _clear_tile_properties_form(self, *, preserve_scroll: bool = False) -> tuple[float, float] | None:
        if not hasattr(self, "tile_properties_form"):
            return None
        yview = self.tile_properties_form.clear(preserve_scroll=preserve_scroll)
        self._tile_property_vars.clear()
        return yview

    def _ensure_tile_behavior_draft(self, tile_num: int, attr0: int, attr1: int, attr2: int, attr3: int) -> dict[str, object]:
        draft = self.selected_tile_behavior_draft
        if draft is None or int(draft.get("tile_num", -1)) != int(tile_num):
            draft = {
                "tile_num": int(tile_num),
                "attr0": int(attr0),
                "attr1": int(attr1),
                "attr2": int(attr2),
                "attr3": int(attr3),
            }
            self.selected_tile_behavior_draft = draft
            self._selected_tile_behavior_draft_dirty = False
        return draft

    def _tile_behavior_display_attrs(self, tile_num: int, attr0: int, attr1: int, attr2: int, attr3: int) -> tuple[int, int, int, int]:
        draft = self._ensure_tile_behavior_draft(tile_num, attr0, attr1, attr2, attr3)
        return int(draft["attr0"]), int(draft["attr1"]), int(draft["attr2"]), int(draft["attr3"])

    def _tile_behavior_side_label(self, value: int) -> str:
        return {0: "None", 1: "Solid", 2: "Deadly"}.get(int(value), f"Unknown {value}")

    def _tile_behavior_top_label(self, value: int) -> str:
        return {
            0: "None",
            1: "Solid",
            2: "Slightly slippery",
            3: "Slippery",
            4: "Very slippery",
            5: "Drop-through",
            6: "Deadly",
        }.get(int(value), f"Unknown {value}")

    def _tile_behavior_bottom_label(self, value: int) -> str:
        return {0: "None", 1: "Solid", 2: "Deadly"}.get(int(value), f"Unknown {value}")

    def _tile_behavior_profile_label(self, attr3: int) -> str:
        bits = int(attr3) & 0x30
        return {
            0x00: "Flat",
            0x10: "Slope down-right",
            0x20: "Slope up-right",
        }.get(bits, f"Unknown profile 0x{bits:02X}")

    def _stage_tile_behavior_choice(self, field: str, label: str) -> None:
        context = self.selected_tile_context
        draft = self.selected_tile_behavior_draft
        if context is None or draft is None:
            raise ValueError("Select a tile definition before editing behavior.")
        if field == "attr0":
            mapping = {"None": 0, "Solid": 1, "Deadly": 2}
            if label not in mapping:
                raise ValueError(f"Unsupported side behavior {label!r}.")
            draft["attr0"] = mapping[label]
        elif field == "attr1":
            mapping = {
                "None": 0,
                "Solid": 1,
                "Slightly slippery": 2,
                "Slippery": 3,
                "Very slippery": 4,
                "Drop-through": 5,
                "Deadly": 6,
            }
            if label not in mapping:
                raise ValueError(f"Unsupported top behavior {label!r}.")
            draft["attr1"] = mapping[label]
        elif field == "bottom":
            mapping = {"None": 0, "Solid": 1, "Deadly": 2}
            if label not in mapping:
                raise ValueError(f"Unsupported bottom behavior {label!r}.")
            draft["attr2"] = (int(draft["attr2"]) & 0xF0) | mapping[label]
        elif field == "profile":
            mapping = {"Flat": 0x00, "Slope down-right": 0x10, "Slope up-right": 0x20}
            if label not in mapping:
                raise ValueError(f"Unsupported surface shape {label!r}.")
            draft["attr3"] = (int(draft["attr3"]) & 0xCF) | mapping[label]
        else:
            raise ValueError(f"Unknown tile behavior field {field!r}.")
        self._selected_tile_behavior_draft_dirty = True
        self.status_text.set("Staged tile behavior edit. Apply writes the tile definition change.")
        self._update_tile_properties_action_bar()
        if not self._suspend_editor_detail_refresh:
            self._refresh_tile_properties_form(context, preserve_scroll=True)

    def _stage_tile_behavior_base_y(self, raw: str) -> None:
        context = self.selected_tile_context
        draft = self.selected_tile_behavior_draft
        if context is None or draft is None:
            raise ValueError("Select a tile definition before editing behavior.")
        base = self._parse_editor_int(raw, minimum=0, maximum=15, label="Surface base Y")
        draft["attr3"] = (int(draft["attr3"]) & 0xF0) | base
        self._selected_tile_behavior_draft_dirty = True
        self.status_text.set("Staged tile surface base edit. Apply writes the tile definition change.")
        self._update_tile_properties_action_bar()
        if not self._suspend_editor_detail_refresh:
            self._refresh_tile_properties_form(context, preserve_scroll=True)

    def _stage_tile_attr2_flag(self, bit: int, raw: str, label: str) -> None:
        context = self.selected_tile_context
        draft = self.selected_tile_behavior_draft
        if context is None or draft is None:
            raise ValueError("Select a tile definition before editing behavior.")
        enabled = raw == "1"
        attr2 = int(draft["attr2"]) & 0xFF
        draft["attr2"] = (attr2 | bit) if enabled else (attr2 & ~bit)
        self._selected_tile_behavior_draft_dirty = True
        self.status_text.set(f"Staged tile {label} edit. Apply writes the tile definition change.")
        self._update_tile_properties_action_bar()
        if not self._suspend_editor_detail_refresh:
            self._refresh_tile_properties_form(context, preserve_scroll=True)

    @staticmethod
    def _replace_attribute_byte(raw: bytes, index: int, value: int) -> bytes:
        patched = bytearray(raw)
        patched[int(index) & 0xFF] = int(value) & 0xFF
        return bytes(patched)

    def _apply_tile_behavior_draft(self) -> None:
        self._commit_all_editor_edit_widgets()
        if self.level is None or self.selected_tile_context is None or self.selected_tile_behavior_draft is None:
            return
        draft = self.selected_tile_behavior_draft
        tile_num = int(draft["tile_num"])
        current = (
            self.level.tile_attributes0[tile_num],
            self.level.tile_attributes1[tile_num],
            self.level.tile_attributes2[tile_num],
            self.level.tile_attributes3[tile_num],
        )
        staged = (int(draft["attr0"]), int(draft["attr1"]), int(draft["attr2"]), int(draft["attr3"]))
        if current == staged:
            self._selected_tile_behavior_draft_dirty = False
            self.status_text.set("No staged tile behavior changes to apply.")
            self._refresh_tile_properties_form(self.selected_tile_context)
            return
        self._record_undo_state()
        self.level.tile_attributes0 = self._replace_attribute_byte(self.level.tile_attributes0, tile_num, staged[0])
        self.level.tile_attributes1 = self._replace_attribute_byte(self.level.tile_attributes1, tile_num, staged[1])
        self.level.tile_attributes2 = self._replace_attribute_byte(self.level.tile_attributes2, tile_num, staged[2])
        self.level.tile_attributes3 = self._replace_attribute_byte(self.level.tile_attributes3, tile_num, staged[3])
        self._selected_tile_behavior_draft_dirty = False
        self.selected_tile_behavior_draft = {
            "tile_num": tile_num,
            "attr0": staged[0],
            "attr1": staged[1],
            "attr2": staged[2],
            "attr3": staged[3],
        }
        self.selected_tile_context.update({"attr0": staged[0], "attr1": staged[1], "attr2": staged[2], "attr3": staged[3]})
        self._load_physics_lab_values(*staged)
        self._mark_dirty(f"Applied tile 0x{tile_num:02X} behavior. Every map cell using this tile definition inherits it.")
        self._refresh_physics_lab_presets()
        self._refresh_tile_properties_form(self.selected_tile_context)
        self._redraw_level_overlays()
        self._refresh_info()

    def _discard_tile_behavior_draft(self) -> None:
        if self.level is None or self.selected_tile_context is None:
            return
        tile_num = int(self.selected_tile_context["tile_num"])
        attr0 = self.level.tile_attributes0[tile_num]
        attr1 = self.level.tile_attributes1[tile_num]
        attr2 = self.level.tile_attributes2[tile_num]
        attr3 = self.level.tile_attributes3[tile_num]
        self.selected_tile_behavior_draft = {"tile_num": tile_num, "attr0": attr0, "attr1": attr1, "attr2": attr2, "attr3": attr3}
        self._selected_tile_behavior_draft_dirty = False
        self.selected_tile_context.update({"attr0": attr0, "attr1": attr1, "attr2": attr2, "attr3": attr3})
        self.status_text.set("Discarded staged tile behavior edits.")
        self._refresh_tile_properties_form(self.selected_tile_context)

    def _add_tile_behavior_apply_bar(self, parent: tk.Misc) -> None:
        bar = ttk.LabelFrame(parent, text="Staged tile behavior edit", padding=6)
        bar.pack(fill="x", padx=2, pady=(0, 6))
        status = (
            "Changes are waiting for Apply. Tile behaviors are shared by every map cell using this tile ID."
            if self._selected_tile_behavior_draft_dirty
            else "Fields edit this tile definition draft; Apply writes one undoable behavior change."
        )
        ttk.Label(bar, text=status, wraplength=420, justify="left").pack(fill="x", anchor="w", pady=(0, 5))
        buttons = ttk.Frame(bar)
        buttons.pack(fill="x")
        apply_button = ttk.Button(buttons, text="Apply", command=self._apply_tile_behavior_draft)
        apply_button.bind("<ButtonPress-1>", lambda _event: self._commit_all_editor_edit_widgets(), add="+")
        apply_button.pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="Discard", command=self._discard_tile_behavior_draft).pack(side="left", fill="x", expand=True, padx=(6, 0))

    def _update_tile_properties_action_bar(self) -> None:
        if not hasattr(self, "tile_properties_action_status"):
            return
        if self.selected_tile_context is None:
            self.tile_properties_action_status.set(
                "Select a map tile to edit its tile definition and secret mechanic."
            )
            if hasattr(self, "tile_properties_apply_button"):
                self.tile_properties_apply_button.configure(state="disabled")
                self.tile_properties_discard_button.configure(state="disabled")
            return
        behavior_dirty = bool(getattr(self, "_selected_tile_behavior_draft_dirty", False))
        secret_dirty = bool(getattr(self, "_selected_tile_secret_draft_dirty", False))
        if behavior_dirty and secret_dirty:
            status = "Tile behavior and secret mechanic changes are waiting for Apply."
        elif behavior_dirty:
            status = "Tile behavior changes are waiting for Apply. They affect every map cell using this tile ID."
        elif secret_dirty:
            status = "Secret mechanic changes are waiting for Apply. Type=None removes the secret from this map cell."
        else:
            status = "Edit any Tile + Secret field, then Apply once to write one undoable change."
        self.tile_properties_action_status.set(status)
        if hasattr(self, "tile_properties_apply_button"):
            self.tile_properties_apply_button.configure(state="normal")
            self.tile_properties_discard_button.configure(state="normal")

    def _apply_tile_properties_drafts(self) -> None:
        """Commit the whole Tile + Secret inspector as one editor action.

        Tile behavior is a tile-definition edit (shared by all map cells that use
        the tile ID), while secret mechanics are per-map-cell bonus records.  The
        UI presents them together because they are authored from the same tile
        inspector; Apply therefore stages both and records one undo snapshot.
        """
        self._commit_all_editor_edit_widgets()
        if self.level is None or self.selected_tile_context is None:
            return

        context = self.selected_tile_context
        behavior_draft = self.selected_tile_behavior_draft
        behavior_changed = False
        tile_num = None
        behavior_staged: tuple[int, int, int, int] | None = None
        if behavior_draft is not None:
            tile_num = int(behavior_draft["tile_num"])
            behavior_current = (
                int(self.level.tile_attributes0[tile_num]),
                int(self.level.tile_attributes1[tile_num]),
                int(self.level.tile_attributes2[tile_num]),
                int(self.level.tile_attributes3[tile_num]),
            )
            behavior_staged = (
                int(behavior_draft["attr0"]),
                int(behavior_draft["attr1"]),
                int(behavior_draft["attr2"]),
                int(behavior_draft["attr3"]),
            )
            behavior_changed = behavior_current != behavior_staged

        secret_draft = self._ensure_tile_secret_draft(context)
        map_xy = secret_draft.get("map_xy")
        secret_changed = False
        secret_mode = str(secret_draft.get("mode", "none"))
        secret_slot = secret_draft.get("slot")
        secret_slot_index = int(secret_slot) if secret_slot is not None else None
        secret_tilemap_pos = None
        secret_desired: tuple[int, int, int, int] | None = None
        secret_create_slot: int | None = None
        if isinstance(map_xy, tuple) and len(map_xy) == 2:
            tx, ty = int(map_xy[0]), int(map_xy[1])
            secret_tilemap_pos = (ty << 8) | tx
            if secret_mode == "none":
                secret_changed = secret_slot_index is not None
            else:
                if secret_slot_index is None:
                    free_slot = self._first_inactive_slot(self.level.bonuses)
                    if free_slot is None:
                        self.status_text.set("No free secret / bonus slots remain in this level.")
                        self._update_tile_properties_action_bar()
                        return
                    secret_create_slot = int(free_slot)
                    compare_slot = secret_create_slot
                else:
                    compare_slot = secret_slot_index
                count = self._tile_secret_count_byte(secret_mode, int(secret_draft.get("hit_count", 1)))
                initial_tile = int(secret_draft.get("initial_tile", secret_draft.get("tile_num", 0x7E))) & 0xFF
                revealed_tile = int(secret_draft.get("revealed_tile", secret_draft.get("tile_num", 0x7E))) & 0xFF
                secret_desired = (initial_tile, revealed_tile, count, int(secret_tilemap_pos))
                bonus = self.level.bonuses[compare_slot]
                secret_current = (int(bonus.tile_num0), int(bonus.tile_num1), int(bonus.count), int(bonus.pos))
                secret_changed = secret_current != secret_desired or secret_slot_index is None
        elif self._selected_tile_secret_draft_dirty:
            self.status_text.set("Secret mechanics can only be attached to a concrete map cell.")
            self._update_tile_properties_action_bar()
            return

        if not behavior_changed and not secret_changed:
            self._selected_tile_behavior_draft_dirty = False
            self._selected_tile_secret_draft_dirty = False
            self.status_text.set("No staged Tile + Secret changes to apply.")
            self._refresh_tile_properties_form(context, preserve_scroll=True)
            return

        self._record_undo_state()
        applied_parts: list[str] = []

        if behavior_changed and tile_num is not None and behavior_staged is not None:
            self.level.tile_attributes0 = self._replace_attribute_byte(self.level.tile_attributes0, tile_num, behavior_staged[0])
            self.level.tile_attributes1 = self._replace_attribute_byte(self.level.tile_attributes1, tile_num, behavior_staged[1])
            self.level.tile_attributes2 = self._replace_attribute_byte(self.level.tile_attributes2, tile_num, behavior_staged[2])
            self.level.tile_attributes3 = self._replace_attribute_byte(self.level.tile_attributes3, tile_num, behavior_staged[3])
            self.selected_tile_behavior_draft = {
                "tile_num": tile_num,
                "attr0": behavior_staged[0],
                "attr1": behavior_staged[1],
                "attr2": behavior_staged[2],
                "attr3": behavior_staged[3],
            }
            context.update({
                "attr0": behavior_staged[0],
                "attr1": behavior_staged[1],
                "attr2": behavior_staged[2],
                "attr3": behavior_staged[3],
            })
            self._load_physics_lab_values(*behavior_staged)
            applied_parts.append(f"tile 0x{tile_num:02X} behavior")

        if secret_changed and isinstance(map_xy, tuple) and len(map_xy) == 2:
            tx, ty = int(map_xy[0]), int(map_xy[1])
            if secret_mode == "none" and secret_slot_index is not None:
                self.level.bonuses[secret_slot_index].pos = 0xFFFF
                applied_parts.append(f"removed secret S{secret_slot_index} at ({tx}, {ty})")
            elif secret_mode != "none" and secret_desired is not None:
                write_slot = secret_slot_index if secret_slot_index is not None else secret_create_slot
                assert write_slot is not None
                bonus = self.level.bonuses[int(write_slot)]
                bonus.tile_num0, bonus.tile_num1, bonus.count, bonus.pos = secret_desired
                applied_parts.append(
                    ("updated" if secret_slot_index is not None else "added")
                    + f" secret S{int(write_slot)} at ({tx}, {ty})"
                )

        self._selected_tile_behavior_draft_dirty = False
        self.selected_tile_secret_draft = self._build_tile_secret_draft(context)
        self._selected_tile_secret_draft_dirty = False
        detail = "; ".join(applied_parts) if applied_parts else "Tile + Secret edits"
        self._mark_dirty(f"Applied {detail}.")
        if behavior_changed:
            self._refresh_physics_lab_presets()
        if secret_changed:
            self._refresh_tables()
        self._refresh_tile_properties_form(context, preserve_scroll=True)
        self._redraw_level_overlays()
        self._refresh_info()

    def _discard_tile_properties_drafts(self) -> None:
        if self.level is None or self.selected_tile_context is None:
            return
        context = self.selected_tile_context
        tile_num = int(context["tile_num"])
        attr0 = int(self.level.tile_attributes0[tile_num])
        attr1 = int(self.level.tile_attributes1[tile_num])
        attr2 = int(self.level.tile_attributes2[tile_num])
        attr3 = int(self.level.tile_attributes3[tile_num])
        self.selected_tile_behavior_draft = {
            "tile_num": tile_num,
            "attr0": attr0,
            "attr1": attr1,
            "attr2": attr2,
            "attr3": attr3,
        }
        context.update({"attr0": attr0, "attr1": attr1, "attr2": attr2, "attr3": attr3})
        self._selected_tile_behavior_draft_dirty = False
        self.selected_tile_secret_draft = self._build_tile_secret_draft(context)
        self._selected_tile_secret_draft_dirty = False
        self.status_text.set("Discarded staged Tile + Secret edits.")
        self._refresh_tile_properties_form(context, preserve_scroll=True)

    def _secret_indices_for_map_xy(self, map_xy: object) -> list[int]:
        if self.level is None or not isinstance(map_xy, tuple) or len(map_xy) != 2:
            return []
        tx, ty = int(map_xy[0]), int(map_xy[1])
        tilemap_pos = (ty << 8) | tx
        return [idx for idx, bonus in enumerate(self.level.bonuses) if bonus.active and bonus.pos == tilemap_pos]

    @staticmethod
    def _tile_secret_type_label(mode: str) -> str:
        return {
            "none": "None",
            "small_random_bonus": "Small random bonus",
            "tile_reveal": "Reveal / appearing tile",
            "big_random_bonus": "Big random bonus",
        }.get(str(mode), str(mode))

    @staticmethod
    def _tile_secret_mode_from_label(label: str) -> str:
        mode = {
            "None": "none",
            "Small random bonus": "small_random_bonus",
            "Reveal / appearing tile": "tile_reveal",
            "Big random bonus": "big_random_bonus",
        }.get(str(label), str(label))
        if mode not in {"none", "small_random_bonus", "tile_reveal", "big_random_bonus"}:
            raise ValueError(f"Unknown secret type: {label}")
        return mode

    @staticmethod
    def _tile_secret_max_hits(mode: str) -> int:
        return 128 if str(mode) == "big_random_bonus" else 64

    def _build_tile_secret_draft(self, context: dict[str, object]) -> dict[str, object]:
        map_xy = context.get("map_xy")
        tile_num = int(context.get("tile_num", 0x7E)) & 0xFF
        secret_indices = self._secret_indices_for_map_xy(map_xy)
        if secret_indices and self.level is not None:
            slot = int(secret_indices[0])
            bonus = self.level.bonuses[slot]
            return {
                "map_xy": tuple(map_xy) if isinstance(map_xy, tuple) else None,
                "tile_num": tile_num,
                "slot": slot,
                "extra_slots": tuple(int(idx) for idx in secret_indices[1:]),
                "mode": bonus.mode,
                "hit_count": int(bonus.hit_count_estimate),
                "initial_tile": int(bonus.tile_num0) & 0xFF,
                "revealed_tile": int(bonus.tile_num1) & 0xFF,
            }
        return {
            "map_xy": tuple(map_xy) if isinstance(map_xy, tuple) else None,
            "tile_num": tile_num,
            "slot": None,
            "extra_slots": (),
            "mode": "none",
            "hit_count": 1,
            "initial_tile": tile_num,
            "revealed_tile": tile_num,
        }

    def _ensure_tile_secret_draft(self, context: dict[str, object]) -> dict[str, object]:
        expected_xy = context.get("map_xy")
        expected_tile = int(context.get("tile_num", 0x7E)) & 0xFF
        draft = self.selected_tile_secret_draft
        if (
            draft is None
            or draft.get("map_xy") != (tuple(expected_xy) if isinstance(expected_xy, tuple) else None)
            or int(draft.get("tile_num", -1)) != expected_tile
        ):
            draft = self._build_tile_secret_draft(context)
            self.selected_tile_secret_draft = draft
            self._selected_tile_secret_draft_dirty = False
        return draft

    def _stage_tile_secret_type(self, label: str) -> None:
        if self.selected_tile_context is None:
            raise ValueError("Select a map tile before editing its secret mechanic.")
        draft = self._ensure_tile_secret_draft(self.selected_tile_context)
        previous_mode = str(draft.get("mode", "none"))
        new_mode = self._tile_secret_mode_from_label(label)
        if previous_mode == new_mode:
            return
        draft["mode"] = new_mode
        current_tile = int(draft.get("tile_num", self.selected_tile_context.get("tile_num", 0x7E))) & 0xFF
        # Friendly creation defaults only when the cell previously had no secret.
        if previous_mode == "none" and new_mode != "none":
            draft["initial_tile"] = 0x7E if new_mode == "tile_reveal" else current_tile
            draft["revealed_tile"] = current_tile
            draft["hit_count"] = min(max(1, int(draft.get("hit_count", 1))), self._tile_secret_max_hits(new_mode))
        elif new_mode != "none":
            draft["hit_count"] = min(max(1, int(draft.get("hit_count", 1))), self._tile_secret_max_hits(new_mode))
        self._selected_tile_secret_draft_dirty = True
        self.status_text.set("Staged tile secret mechanic type edit. Apply writes the map-cell secret change.")
        self._update_tile_properties_action_bar()
        if not self._suspend_editor_detail_refresh:
            self._refresh_tile_properties_form(self.selected_tile_context, preserve_scroll=True)

    def _stage_tile_secret_hits(self, raw: str) -> None:
        if self.selected_tile_context is None:
            raise ValueError("Select a map tile before editing its secret mechanic.")
        draft = self._ensure_tile_secret_draft(self.selected_tile_context)
        mode = str(draft.get("mode", "none"))
        max_hits = self._tile_secret_max_hits(mode)
        draft["hit_count"] = self._parse_editor_int(raw, minimum=1, maximum=max_hits, label="Secret hit count")
        self._selected_tile_secret_draft_dirty = True
        self.status_text.set("Staged secret hit-count edit. Apply writes the map-cell secret change.")
        self._update_tile_properties_action_bar()
        if not self._suspend_editor_detail_refresh:
            self._refresh_tile_properties_form(self.selected_tile_context, preserve_scroll=True)

    def _stage_tile_secret_tile_value(self, field: str, raw: str) -> None:
        if self.selected_tile_context is None:
            raise ValueError("Select a map tile before editing its secret mechanic.")
        if field not in {"initial_tile", "revealed_tile"}:
            raise ValueError(f"Unknown secret tile field: {field}")
        draft = self._ensure_tile_secret_draft(self.selected_tile_context)
        draft[field] = self._parse_editor_int(raw, minimum=0, maximum=255, label="Secret tile ID")
        self._selected_tile_secret_draft_dirty = True
        self.status_text.set("Staged secret tile visual edit. Apply writes the map-cell secret change.")
        self._update_tile_properties_action_bar()
        if not self._suspend_editor_detail_refresh:
            self._refresh_tile_properties_form(self.selected_tile_context, preserve_scroll=True)

    def _tile_secret_count_byte(self, mode: str, hits: int) -> int:
        hits = max(1, int(hits))
        if mode == "big_random_bonus":
            return 0x80 | min(hits - 1, 0x7F)
        if mode == "tile_reveal":
            return 0x40 | min(hits - 1, 0x3F)
        if mode == "small_random_bonus":
            return min(hits - 1, 0x3F)
        raise ValueError(f"Cannot encode secret mode {mode!r}.")

    def _apply_tile_secret_draft(self) -> None:
        self._commit_all_editor_edit_widgets()
        if self.level is None or self.selected_tile_context is None:
            return
        draft = self._ensure_tile_secret_draft(self.selected_tile_context)
        map_xy = draft.get("map_xy")
        if not isinstance(map_xy, tuple) or len(map_xy) != 2:
            self.status_text.set("Secret mechanics can only be attached to a concrete map cell.")
            return
        tx, ty = int(map_xy[0]), int(map_xy[1])
        tilemap_pos = (ty << 8) | tx
        slot = draft.get("slot")
        slot_index = int(slot) if slot is not None else None
        mode = str(draft.get("mode", "none"))
        if mode == "none":
            if slot_index is None:
                self._selected_tile_secret_draft_dirty = False
                self.status_text.set("This tile already has no secret mechanic.")
                self._refresh_tile_properties_form(self.selected_tile_context)
                return
            self._record_undo_state()
            self.level.bonuses[slot_index].pos = 0xFFFF
            self._mark_dirty(f"Removed secret mechanic S{slot_index} from tile ({tx}, {ty}).")
        else:
            if slot_index is None:
                free_slot = self._first_inactive_slot(self.level.bonuses)
                if free_slot is None:
                    self.status_text.set("No free secret / bonus slots remain in this level.")
                    return
                slot_index = int(free_slot)
            count = self._tile_secret_count_byte(mode, int(draft.get("hit_count", 1)))
            initial_tile = int(draft.get("initial_tile", draft.get("tile_num", 0x7E))) & 0xFF
            revealed_tile = int(draft.get("revealed_tile", draft.get("tile_num", 0x7E))) & 0xFF
            bonus = self.level.bonuses[slot_index]
            desired = (initial_tile, revealed_tile, count, tilemap_pos)
            current = (int(bonus.tile_num0), int(bonus.tile_num1), int(bonus.count), int(bonus.pos))
            if current == desired:
                self._selected_tile_secret_draft_dirty = False
                self.status_text.set("No staged secret mechanic changes to apply.")
                self._refresh_tile_properties_form(self.selected_tile_context)
                return
            self._record_undo_state()
            bonus.tile_num0 = initial_tile
            bonus.tile_num1 = revealed_tile
            bonus.count = count
            bonus.pos = tilemap_pos
            verb = "Updated" if slot is not None else "Added"
            self._mark_dirty(f"{verb} secret mechanic S{slot_index} on tile ({tx}, {ty}).")
        # Rebuild the draft from live data after Apply so Type=None behaves like
        # the normal steady state for an empty map cell.
        self.selected_tile_secret_draft = self._build_tile_secret_draft(self.selected_tile_context)
        self._selected_tile_secret_draft_dirty = False
        self._refresh_tables()
        self._refresh_tile_properties_form(self.selected_tile_context)
        self._redraw_level_overlays()
        self._refresh_info()

    def _discard_tile_secret_draft(self) -> None:
        if self.selected_tile_context is None:
            return
        self.selected_tile_secret_draft = self._build_tile_secret_draft(self.selected_tile_context)
        self._selected_tile_secret_draft_dirty = False
        self.status_text.set("Discarded staged secret mechanic edits.")
        self._refresh_tile_properties_form(self.selected_tile_context)

    def _add_tile_secret_apply_bar(self, parent: tk.Misc) -> None:
        bar = ttk.LabelFrame(parent, text="Staged secret mechanic edit", padding=6)
        bar.grid(row=97, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        bar.columnconfigure(0, weight=1)
        status = (
            "Changes are waiting for Apply. Type=None removes the secret from this map cell."
            if self._selected_tile_secret_draft_dirty
            else "Every map cell has an implicit Secret type. None means no active secret; Apply creates, updates, or removes it."
        )
        ttk.Label(bar, text=status, wraplength=400, justify="left").pack(fill="x", anchor="w", pady=(0, 5))
        buttons = ttk.Frame(bar)
        buttons.pack(fill="x")
        apply_button = ttk.Button(buttons, text="Apply", command=self._apply_tile_secret_draft)
        apply_button.bind("<ButtonPress-1>", lambda _event: self._commit_all_editor_edit_widgets(), add="+")
        apply_button.pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="Discard", command=self._discard_tile_secret_draft).pack(side="left", fill="x", expand=True, padx=(6, 0))

    def _refresh_tile_properties_form(self, context: dict[str, object] | None, *, preserve_scroll: bool = False) -> None:
        if not hasattr(self, "tile_properties_form"):
            return
        yview = self._clear_tile_properties_form(preserve_scroll=preserve_scroll)
        parent = self.tile_properties_form.inner
        self._update_tile_properties_action_bar()
        if context is None:
            self._editor_note(parent, "Click a tile in the Level tab. Secrets are treated as tile-bound mechanics here, next to physics and animation flags.")
            self.tile_properties_form.restore_yview_after_layout(yview)
            return

        tile_num = int(context["tile_num"])
        lut = int(context["lut"])
        map_xy = context.get("map_xy")
        raw_attr0 = int(context["attr0"])
        raw_attr1 = int(context["attr1"])
        raw_attr2 = int(context["attr2"])
        raw_attr3 = int(context["attr3"])
        attr0, attr1, attr2, attr3 = self._tile_behavior_display_attrs(tile_num, raw_attr0, raw_attr1, raw_attr2, raw_attr3)
        underside = attr2 & 0x0F
        side_value = self._tile_behavior_side_label(attr0)
        top_value = self._tile_behavior_top_label(attr1)
        bottom_value = self._tile_behavior_bottom_label(underside)
        profile_value = self._tile_behavior_profile_label(attr3)

        identity = self._editor_section(parent, "Tile")
        self._add_editor_entry(identity, "Tile ID", f"0x{tile_num:02X}", 0, kind="tile")
        self._add_editor_entry(identity, "Map cell", str(map_xy), 1, kind="tile")
        self._add_editor_entry(identity, "LUT value", f"0x{lut:04X}", 2, kind="tile")
        collision = self._editor_section(parent, "Collision & surface")
        self._add_editor_combo(collision, "Side behavior", side_value, 0, kind="tile", values=["None", "Solid", "Deadly"], editable=True, on_select=lambda label: self._stage_tile_behavior_choice("attr0", label))
        self._add_editor_combo(collision, "Top behavior", top_value, 1, kind="tile", values=["None", "Solid", "Slightly slippery", "Slippery", "Very slippery", "Drop-through", "Deadly"], editable=True, on_select=lambda label: self._stage_tile_behavior_choice("attr1", label))
        self._add_editor_combo(collision, "Bottom behavior", bottom_value, 2, kind="tile", values=["None", "Solid", "Deadly"], editable=True, on_select=lambda label: self._stage_tile_behavior_choice("bottom", label))
        self._add_editor_combo(collision, "Surface shape", profile_value, 3, kind="tile", values=["Flat", "Slope down-right", "Slope up-right"], editable=True, on_select=lambda label: self._stage_tile_behavior_choice("profile", label))
        self._add_editor_spinbox(collision, "Surface base Y", attr3 & 0x0F, 4, kind="tile", from_=0, to=15, editable=True, on_commit=self._stage_tile_behavior_base_y)

        animated_group = self.level.animated_tile_group_for_tile(tile_num) if self.level is not None else None
        animated_member = animated_group is not None
        animated_phase = self.level.animated_tile_phase(tile_num) if self.level is not None else None

        dynamic = self._editor_section(parent, "Dynamic / visual tile flags")
        self._add_readonly_checkbox(dynamic, "Animated tile group member", animated_member, future_editable=False, note="Derived from a 3-tile animation group base.", kind="tile")
        self._add_readonly_checkbox(dynamic, "Animation group starts on this tile", bool(attr2 & 0x80), future_editable=True, note="This authored attr2 0x80 marker defines a 3-tile animation group base.", kind="tile", on_toggle=lambda raw: self._stage_tile_attr2_flag(0x80, raw, "animation-group base"))
        self._add_readonly_checkbox(dynamic, "Foreground / front-mask overlay", bool(attr2 & 0x40), future_editable=True, kind="tile", on_toggle=lambda raw: self._stage_tile_attr2_flag(0x40, raw, "foreground/front-mask"))
        self._add_readonly_checkbox(dynamic, "Player-step decorative tile cycle", bool(attr2 & 0x20), future_editable=True, note="Runtime increments/decrements the placed tile ID while the player enters/leaves it.", kind="tile", on_toggle=lambda raw: self._stage_tile_attr2_flag(0x20, raw, "player-step tile-cycle"))
        self._add_readonly_checkbox(dynamic, "Fly emitter", bool(attr2 & 0x10), future_editable=True, note="Runtime spawns the small fly effect when the player touches this tile.", kind="tile", on_toggle=lambda raw: self._stage_tile_attr2_flag(0x10, raw, "fly-emitter"))

        if animated_group is not None:
            animation = self._editor_section(parent, "Animated tile group")
            members = animated_group.members
            frame_tiles = [self.level.tile_for_animation_frame(tile_num, frame) for frame in range(3)]
            self._add_editor_entry(animation, "Group base tile", f"0x{animated_group.base_tile:02X}", 0, kind="tile")
            self._add_editor_entry(animation, "Group members", ", ".join(f"0x{member:02X}" for member in members), 1, kind="tile")
            self._add_editor_entry(animation, "Selected member phase", f"{animated_phase if animated_phase is not None else '—'} / 2", 2, kind="tile")
            self._add_editor_entry(animation, "Visual sequence for this map tile", " → ".join(f"0x{member:02X}" for member in frame_tiles), 3, kind="tile")
            self._add_editor_entry(animation, "Current viewer frame", self.animation_frame_label.get(), 4, kind="tile")

        secrets = self._editor_section(parent, "Secret mechanic for this map cell")
        secret_draft = self._ensure_tile_secret_draft(context)
        secret_mode = str(secret_draft.get("mode", "none"))
        secret_type_values = [
            "None",
            "Small random bonus",
            "Reveal / appearing tile",
            "Big random bonus",
        ]
        self._add_editor_combo(
            secrets,
            "Type",
            self._tile_secret_type_label(secret_mode),
            0,
            kind="tile",
            values=secret_type_values,
            editable=True,
            on_select=self._stage_tile_secret_type,
        )
        if secret_draft.get("slot") is not None:
            self._add_editor_entry(secrets, "Backing slot", f"S{int(secret_draft['slot'])}", 1, kind="tile")
            content_row = 2
        else:
            self._add_editor_entry(secrets, "Backing slot", "Allocated on Apply if Type is not None", 1, kind="tile")
            content_row = 2
        max_hits = self._tile_secret_max_hits(secret_mode)
        self._add_editor_spinbox(
            secrets,
            "Hit count",
            min(max(1, int(secret_draft.get("hit_count", 1))), max_hits),
            content_row,
            kind="tile",
            from_=1,
            to=max_hits,
            editable=True,
            on_commit=self._stage_tile_secret_hits,
        )
        self._add_editor_entry(
            secrets,
            "Initial tile",
            f"0x{int(secret_draft.get('initial_tile', tile_num)) & 0xFF:02X}",
            content_row + 1,
            kind="tile",
            editable=True,
            on_commit=lambda raw: self._stage_tile_secret_tile_value("initial_tile", raw),
        )
        self._add_editor_entry(
            secrets,
            "Revealed tile",
            f"0x{int(secret_draft.get('revealed_tile', tile_num)) & 0xFF:02X}",
            content_row + 2,
            kind="tile",
            editable=True,
            on_commit=lambda raw: self._stage_tile_secret_tile_value("revealed_tile", raw),
        )
        if secret_mode == "none":
            self._editor_note(secrets, "Type=None means this block has no active secret. Pick a type and Apply to create one with sensible defaults.")
        elif secret_mode == "tile_reveal":
            self._editor_note(secrets, "Reveal secrets default to initial tile 0x7E and reveal this tile, but both tile IDs are editable here.")
        else:
            self._editor_note(secrets, "Bonus secrets default to the block's current tile for both initial and revealed visuals; edit them here when needed.")
        extra_slots = tuple(secret_draft.get("extra_slots", ()))
        if extra_slots:
            self._editor_note(secrets, "Multiple active secret slots target this same tile. This inline editor changes the first slot; the remaining slots stay untouched: " + ", ".join(f"S{idx}" for idx in extra_slots))
        raw = self._editor_section(parent, "Advanced / raw")
        self._add_editor_entry(raw, "attr0", f"0x{attr0:02X}", 0, kind="tile")
        self._add_editor_entry(raw, "attr1", f"0x{attr1:02X}", 1, kind="tile")
        self._add_editor_entry(raw, "attr2", f"0x{attr2:02X}", 2, kind="tile")
        self._add_editor_entry(raw, "attr3", f"0x{attr3:02X}", 3, kind="tile")

        self.tile_properties_form.restore_yview_after_layout(yview)

    def _refresh_physics_lab_presets(self) -> None:
        if not hasattr(self, "physics_preset_combo"):
            return
        if self.level is None:
            self.physics_preset_combo.configure(values=[])
            self.physics_preset_var.set("")
            return
        combos: dict[tuple[int, int, int, int], list[int]] = {}
        for tile_num in sorted(set(self.level.tilemap)):
            combo = (
                self.level.tile_attributes0[tile_num],
                self.level.tile_attributes1[tile_num],
                self.level.tile_attributes2[tile_num] & 0x0F,
                self.level.tile_attributes3[tile_num],
            )
            combos.setdefault(combo, []).append(tile_num)
        labels = []
        self._physics_preset_lookup: dict[str, tuple[int, int, int, int]] = {}
        for (attr0, attr1, attr2b, attr3), tiles in sorted(combos.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[0][3])):
            tile_preview = ", ".join(f"{tile:02X}" for tile in tiles[:5])
            if len(tiles) > 5:
                tile_preview += ", …"
            label = (
                f"tiles {tile_preview} — side: {describe_tile_side_behavior(attr0)} | "
                f"top: {describe_tile_top_behavior(attr1)} | profile: {describe_tile_floor_profile(attr3)} | bottom={attr2b}"
            )
            labels.append(label)
            self._physics_preset_lookup[label] = (attr0, attr1, attr2b, attr3)
        self.physics_preset_combo.configure(values=labels)
        if labels and not self.physics_preset_var.get():
            self.physics_preset_var.set(labels[0])
            self._on_physics_preset_selected()
        elif not labels:
            self.physics_preset_var.set("")
            self._redraw_physics_lab()

    def _on_physics_preset_selected(self, _event=None) -> None:
        label = self.physics_preset_var.get()
        combo = getattr(self, "_physics_preset_lookup", {}).get(label)
        if combo is None:
            return
        attr0, attr1, attr2b, attr3 = combo
        self._load_physics_lab_values(attr0, attr1, attr2b, attr3)

    def _redraw_physics_lab(self) -> None:
        if not hasattr(self, "physics_lab_canvas"):
            return
        c = self.physics_lab_canvas
        c.delete("all")
        w = max(1, c.winfo_width() or 240)
        h = max(1, c.winfo_height() or 240)
        tile_size = min(w, h) - 60
        tile_size = max(96, min(176, tile_size))
        x0 = (w - tile_size) / 2
        y0 = (h - tile_size) / 2
        x1 = x0 + tile_size
        y1 = y0 + tile_size
        scale_px = tile_size / 16.0
        c.create_rectangle(x0, y0, x1, y1, outline="#5A5A5A", width=2)
        c.create_text(w / 2, y0 - 14, text="16×16 tile preview", fill="#CFCFCF")

        attr0 = self._physics_side_value()
        attr1 = self._physics_top_value()
        underside = self._physics_bottom_value()
        attr3 = self._physics_attr3_value()
        base = attr3 & 0x0F
        slope = attr3 & 0x30

        def map_point(px: float, py: float) -> tuple[float, float]:
            return (x0 + px * scale_px, y0 + py * scale_px)

        def profile_points() -> list[tuple[float, float]]:
            pts = []
            for lx in range(0, 16):
                if slope == 0x10:
                    ly = base + (lx // 3)
                elif slope == 0x20:
                    ly = base + ((15 - lx) // 3)
                else:
                    ly = base
                ly = max(0, min(15, ly))
                pts.append(map_point(lx + 0.5, ly + 0.5))
            return pts

        surface = profile_points()
        semantic_fill = {1: "#46E58C", 2: "#78E8FF", 3: "#78E8FF", 4: "#78E8FF", 5: "#FFD65A", 6: "#FF5B5B"}.get(attr1)
        if attr1 != 0:
            flat_line = [coord for pt in surface for coord in pt]
            if attr1 == 5:
                c.create_line(*flat_line, fill=semantic_fill, width=4, dash=(6, 4), capstyle=tk.ROUND, joinstyle=tk.ROUND)
            else:
                c.create_line(*flat_line, fill=semantic_fill, width=4, capstyle=tk.ROUND, joinstyle=tk.ROUND)
            if attr1 in (2, 3, 4):
                step = {2: 6, 3: 4, 4: 3}[attr1]
                for i in range(2, len(surface) - 1, step):
                    x, y = surface[i]
                    c.create_line(x - 10, y + 12, x + 10, y - 4, fill=semantic_fill, width=2)
            elif attr1 == 5:
                for i in range(4, len(surface), 7):
                    x, y = surface[i]
                    c.create_line(x, y + 3, x, y + 18, fill=semantic_fill, width=2)
                    c.create_line(x - 5, y + 12, x, y + 18, x + 5, y + 12, fill=semantic_fill, width=2)
            elif attr1 == 6:
                for i in range(2, len(surface)-1, 5):
                    x, y = surface[i]
                    c.create_line(x - 6, y, x, y - 10, x + 6, y, fill=semantic_fill, width=2)
        elif attr3 != 0:
            flat_line = [coord for pt in surface for coord in pt]
            c.create_line(*flat_line, fill="#B6F0B6", width=2, dash=(4, 4), capstyle=tk.ROUND, joinstyle=tk.ROUND)

        if slope == 0x10:
            c.create_line(*map_point(3, base + 1), *map_point(13, min(15, base + 4)), arrow=tk.LAST, fill="#FFFFFF", width=2)
        elif slope == 0x20:
            c.create_line(*map_point(3, max(0, base + 4)), *map_point(13, base + 1), arrow=tk.LAST, fill="#FFFFFF", width=2)

        solid_color = "#46E58C"
        deadly_color = "#FF5B5B"
        if attr0 == 1:
            c.create_line(*map_point(1.2, 1), *map_point(1.2, 15), fill=solid_color, width=4)
            c.create_line(*map_point(14.8, 1), *map_point(14.8, 15), fill=solid_color, width=4)
        elif attr0 == 2:
            pts_left = []
            for i in range(0, 9):
                y = 1 + i * (14 / 8)
                x = 1.2 if i % 2 == 0 else 4.0
                pts_left.extend(map_point(x, y))
            pts_right = []
            for i in range(0, 9):
                y = 1 + i * (14 / 8)
                x = 14.8 if i % 2 == 0 else 12.0
                pts_right.extend(map_point(x, y))
            c.create_line(*pts_left, fill=deadly_color, width=3)
            c.create_line(*pts_right, fill=deadly_color, width=3)

        if underside == 1:
            c.create_line(*map_point(1, 14.8), *map_point(15, 14.8), fill=solid_color, width=4)
        elif underside == 2:
            pts = []
            for i in range(0, 11):
                x = 1 + i * (14 / 10)
                y = 14.8 if i % 2 == 0 else 12.2
                pts.extend(map_point(x, y))
            c.create_line(*pts, fill=deadly_color, width=3)

        bottom_text = {0: "pass-through underside", 1: "solid underside", 2: "deadly underside"}.get(underside, "special underside value")
        context_lines: list[str] = []
        context = self.selected_tile_context
        if context is not None:
            tile_num = int(context["tile_num"])
            lut = int(context["lut"])
            source = str(context["source"])
            map_xy = context.get("map_xy")
            context_lines.extend([
                f"Selected tile: 0x{tile_num:02X}",
                f"Source: {source}",
                f"Map location: {map_xy}" if map_xy is not None else "Map location: not tied to a map cell",
                f"Tile LUT value: 0x{lut:04X}",
                "",
            ])
        else:
            context_lines.extend(["No tile selected yet.", ""])
        dynamic_flags = []
        raw_attr2 = int(context["attr2"]) if context is not None else underside
        if context is not None and self.level is not None and self.level.is_animated_tile_member(int(context["tile_num"])):
            group = self.level.animated_tile_group_for_tile(int(context["tile_num"]))
            phase = self.level.animated_tile_phase(int(context["tile_num"]))
            if group is not None:
                dynamic_flags.append(f"animated group member (base 0x{group.base_tile:02X}, phase {phase})")
        elif raw_attr2 & 0x80:
            dynamic_flags.append("animated tile group base")
        if raw_attr2 & 0x40:
            dynamic_flags.append("front-mask overlay")
        if raw_attr2 & 0x20:
            dynamic_flags.append("player-step decorative tile cycle")
        if raw_attr2 & 0x10:
            dynamic_flags.append("fly emitter")
        dynamic_text = ", ".join(dynamic_flags) if dynamic_flags else "none detected"
        summary = "\n".join(context_lines + [
            f"Raw attrs: attr0=0x{attr0:02X}, attr1=0x{attr1:02X}, attr2=0x{raw_attr2:02X}, attr3=0x{attr3:02X}",
            "",
            f"Sides: {describe_tile_side_behavior(attr0)}",
            f"Top: {describe_tile_top_behavior(attr1)}",
            f"Profile: {describe_tile_floor_profile(attr3)}",
            f"Bottom: {bottom_text}",
            f"Dynamic flags from attr2: {dynamic_text}",
        ])
        self._set_physics_lab_text(summary)

    def _active_tile_overlay_flags(self) -> set[str]:
        flags: set[str] = set()
        if self.overlay_front_tiles.get():
            flags.add("front")
        if self.overlay_animated.get():
            flags.add("animated")
        if self.overlay_physics_diagram.get():
            flags.add("physics_diagram")
        return flags

    def _active_object_overlay_flags(self, *, visible_overlays_only: bool = True) -> set[str]:
        flags: set[str] = set()
        if not visible_overlays_only or self.overlay_player_start.get():
            flags.add("player_start")
        if not visible_overlays_only or self.overlay_monsters.get():
            flags.add("monsters")
        if not visible_overlays_only or self.overlay_items.get():
            flags.add("items")
        if not visible_overlays_only or self.overlay_platforms.get():
            flags.add("platforms")
        if not visible_overlays_only or self.overlay_gates.get():
            flags.add("gates")
        if not visible_overlays_only or self.overlay_columns.get():
            flags.add("columns")
        if not visible_overlays_only or self.overlay_bonuses.get():
            flags.add("bonuses")
        if not visible_overlays_only or self.overlay_boss.get():
            flags.add("boss")
        return flags

    def _rerender_all(self) -> None:
        self._rerender_level()
        self._rerender_tiles()
        self._rerender_front_tiles()
        self._refresh_info()
        self._refresh_tables()
        self._refresh_physics_lab_presets()
        self._redraw_physics_lab()

    def _chunk_grid_extents(self) -> tuple[int, int]:
        if self.level is None:
            return (0, 0)
        size = self._level_chunk_size_tiles
        return (
            (self.level.width_tiles + size - 1) // size,
            (self.level.height_tiles + size - 1) // size,
        )

    def _render_level_chunk(self, chunk_x: int, chunk_y: int) -> None:
        if self.level is None or not self.union_tiles:
            return
        size = self._level_chunk_size_tiles
        tile_x = chunk_x * size
        tile_y = chunk_y * size
        if tile_x >= self.level.width_tiles or tile_y >= self.level.height_tiles:
            return
        width_tiles = min(size, self.level.width_tiles - tile_x)
        height_tiles = min(size, self.level.height_tiles - tile_y)
        scale = max(1, self.zoom.get())
        image = render_level_chunk_image(
            self.level,
            self.union_tiles,
            self.palettes[self.current_level_index],
            chunk_tile_x=tile_x,
            chunk_tile_y=tile_y,
            chunk_width_tiles=width_tiles,
            chunk_height_tiles=height_tiles,
            front_tiles=self.front_tiles,
            scale=scale,
            show_front_layer=self.front_enabled.get(),
            animation_frame=0,
        )
        self.level_canvas.set_image_chunk(
            (chunk_x, chunk_y),
            image,
            tile_x * 16 * scale,
            tile_y * 16 * scale,
        )

    def _rerender_level(self) -> None:
        if self.level is None or not self.union_tiles:
            return
        try:
            xview = self.level_canvas.canvas.xview()
            yview = self.level_canvas.canvas.yview()
            scale = max(1, self.zoom.get())
            map_w = self.level.width_tiles * 16 * scale
            map_h = self.level.height_tiles * 16 * scale
            self._dirty_level_chunks.clear()
            if self._dirty_level_chunks_after_id is not None:
                try:
                    self.after_cancel(self._dirty_level_chunks_after_id)
                except Exception:
                    pass
                self._dirty_level_chunks_after_id = None
            self.level_canvas.begin_chunked_image(map_w, map_h)
            chunks_x, chunks_y = self._chunk_grid_extents()
            for chunk_y in range(chunks_y):
                for chunk_x in range(chunks_x):
                    self._render_level_chunk(chunk_x, chunk_y)
            if self.background_blob:
                background = render_background_image(
                    self.background_blob,
                    self.palettes[self.current_level_index],
                    scale=1,
                )
                self.level_canvas.set_viewport_background(background)
            else:
                self.level_canvas.set_viewport_background(None)
            if self._center_on_player_start_pending:
                self.after_idle(self._center_level_on_player_start)
            elif xview and yview:
                self.level_canvas.canvas.xview_moveto(xview[0])
                self.level_canvas.canvas.yview_moveto(yview[0])
                self.level_canvas.position_viewport_background()
            self._redraw_level_overlays()
            self._update_tile_place_ghost(None)
            self.status_text.set(
                f"Level {self.level.level_id} [{self.difficulty.get()}]: "
                f"{self.level.width_tiles}×{self.level.height_tiles} tiles, "
                f"{map_w}×{map_h} px at {self.zoom.get()}×, "
                f"chunked map cache {chunks_x}×{chunks_y}, "
                f"monsters visible {len(self._visible_monsters())}/{len(self.level.monsters)}"
            )
        except Exception as exc:
            messagebox.showerror("Render error", str(exc))

    def _tile_chunk_key(self, tx: int, ty: int) -> tuple[int, int]:
        size = self._level_chunk_size_tiles
        return (tx // size, ty // size)

    def _queue_level_chunk_refresh(self, tx: int, ty: int) -> None:
        self._dirty_level_chunks.add(self._tile_chunk_key(tx, ty))
        if self._dirty_level_chunks_after_id is None:
            self._dirty_level_chunks_after_id = self.after_idle(self._flush_dirty_level_chunks)

    def _flush_dirty_level_chunks(self) -> None:
        self._dirty_level_chunks_after_id = None
        if self.level is None or not self._dirty_level_chunks:
            return
        pending = sorted(self._dirty_level_chunks)
        self._dirty_level_chunks.clear()
        for chunk_x, chunk_y in pending:
            self._render_level_chunk(chunk_x, chunk_y)
        # Remove transient per-cell brush patches once their persistent chunks are current.
        state = self._tile_paint_state
        if isinstance(state, dict):
            preview_items = state.get("preview_items")
            if isinstance(preview_items, dict):
                size = self._level_chunk_size_tiles
                pending_set = set(pending)
                for coords, item in list(preview_items.items()):
                    tx, ty = coords
                    if (tx // size, ty // size) in pending_set:
                        self.level_canvas.canvas.delete(item)
                        preview_items.pop(coords, None)
                        self._tile_paint_preview_photos.pop(coords, None)
        self._ensure_tile_place_ghost_above_map()

    def _center_level_on_player_start(self) -> None:
        if self.level is None:
            return
        canvas = self.level_canvas.canvas
        canvas.update_idletasks()
        scale = self.zoom.get()
        world_x = self.level.header.start_x_pos * scale
        world_y = self.level.header.start_y_pos * scale
        view_w = max(1, canvas.winfo_width())
        view_h = max(1, canvas.winfo_height())
        map_w = max(1, self.level.width_tiles * 16 * scale)
        map_h = max(1, self.level.height_tiles * 16 * scale)
        target_x = max(0, min(map_w - view_w, world_x - view_w // 2))
        target_y = max(0, min(map_h - view_h, world_y - view_h // 2))
        canvas.xview_moveto(target_x / map_w)
        canvas.yview_moveto(target_y / map_h)
        self.level_canvas.position_viewport_background()
        self._redraw_animated_tile_overlay()
        self._center_on_player_start_pending = False

    def _rerender_tiles(self) -> None:
        if self.level is None or not self.union_tiles:
            return
        try:
            atlas = render_tile_atlas(
                self.level,
                self.union_tiles,
                self.palettes[self.current_level_index],
                scale=2,
                columns=16,
            )
            self.tile_photo = ImageTk.PhotoImage(atlas)
            self.tile_canvas.set_image(self.tile_photo)
            self._redraw_tile_catalog_selection()
        except Exception as exc:
            messagebox.showerror("Tile atlas error", str(exc))

    def _rerender_front_tiles(self) -> None:
        if self.level is None or not self.front_tiles:
            return
        try:
            atlas = render_front_tile_atlas(
                self.level,
                self.front_tiles,
                self.palettes[self.current_level_index],
                scale=2,
                columns=16,
            )
            self.front_tile_photo = ImageTk.PhotoImage(atlas)
            self.front_tile_canvas.set_image(self.front_tile_photo)
        except Exception as exc:
            messagebox.showerror("Front tile atlas error", str(exc))

    def _visible_tile_bounds(self, *, pad_tiles: int = 1) -> tuple[int, int, int, int]:
        canvas = self.level_canvas.canvas
        scale = max(1, self.zoom.get())
        tile_px = 16 * scale
        x0 = int(canvas.canvasx(0) // tile_px) - pad_tiles
        y0 = int(canvas.canvasy(0) // tile_px) - pad_tiles
        x1 = int(canvas.canvasx(max(1, canvas.winfo_width())) // tile_px) + 1 + pad_tiles
        y1 = int(canvas.canvasy(max(1, canvas.winfo_height())) // tile_px) + 1 + pad_tiles
        if self.level is None:
            return 0, 0, 0, 0
        return (
            max(0, x0),
            max(0, y0),
            min(self.level.width_tiles, x1),
            min(self.level.height_tiles, y1),
        )

    def _redraw_animated_tile_overlay(self) -> None:
        if not hasattr(self, "level_canvas") or self.level is None:
            return
        canvas = self.level_canvas.canvas
        canvas.delete("animated_tile_overlay")
        self._animated_tile_overlay_photos.clear()
        if not self.animate_tiles.get() or not self.union_tiles:
            return
        frame = self._animation_frame_index()
        if frame == 0:
            return
        scale = max(1, self.zoom.get())
        tx0, ty0, tx1, ty1 = self._visible_tile_bounds(pad_tiles=2)
        tile_cache: dict[int, ImageTk.PhotoImage] = {}
        for ty in range(ty0, ty1):
            row_off = ty * self.level.width_tiles
            for tx in range(tx0, tx1):
                source_tile = self.level.tilemap[row_off + tx]
                if not self.level.is_animated_tile_member(source_tile):
                    continue
                tile_num = self.level.tile_for_animation_frame(source_tile, frame)
                photo = tile_cache.get(tile_num)
                if photo is None:
                    tile = render_tile_image(
                        self.level,
                        self.union_tiles,
                        self.palettes[self.current_level_index],
                        tile_num,
                        scale=scale,
                        transparent_zero=True,
                    )
                    photo = ImageTk.PhotoImage(tile)
                    tile_cache[tile_num] = photo
                    self._animated_tile_overlay_photos.append(photo)
                canvas.create_image(
                    tx * 16 * scale,
                    ty * 16 * scale,
                    image=photo,
                    anchor="nw",
                    tags=("animated_tile_overlay",),
                )
        if canvas.find_withtag("overlay"):
            canvas.tag_lower("animated_tile_overlay", "overlay")
        else:
            canvas.tag_raise("animated_tile_overlay")

    def _redraw_level_overlays(self) -> None:
        if not hasattr(self, "level_canvas") or self.level is None:
            return
        canvas = self.level_canvas.canvas
        canvas.delete("overlay")
        canvas.delete("animated_tile_overlay")
        self._level_overlay_photos.clear()
        self._animated_tile_overlay_photos.clear()
        scale = self.zoom.get()
        tile_px = 16 * scale
        map_w = self.level.width_tiles * tile_px
        map_h = self.level.height_tiles * tile_px

        def rect_world(x0: int, y0: int, x1: int, y1: int, *, outline: str, fill: str = "", width: int = 1, stipple: str | None = None) -> None:
            kwargs = {"outline": outline, "width": width, "tags": ("overlay",)}
            if fill:
                kwargs["fill"] = fill
            if stipple:
                kwargs["stipple"] = stipple
            canvas.create_rectangle(x0 * scale, y0 * scale, x1 * scale, y1 * scale, **kwargs)

        def text_world(x: int, y: int, text: str, *, fill: str = "#FFFFFF", anchor: str = "nw") -> None:
            canvas.create_text(x * scale, y * scale, text=text, fill=fill, anchor=anchor, tags=("overlay",))

        def marker_world(x: int, y: int, label: str, *, color: str) -> None:
            px = x * scale
            py = y * scale
            r = 5
            canvas.create_oval(px - r, py - r, px + r, py + r, fill=color, outline="#FFFFFF", width=1, tags=("overlay",))
            canvas.create_text(px + r + 3, py - r - 2, text=label, fill="#FFFFFF", anchor="nw", tags=("overlay",))

        def arrow_world(x0: float, y0: float, x1: float, y1: float, *, fill: str, width: int = 3, dash=None) -> None:
            kwargs = {"fill": fill, "width": width, "arrow": tk.LAST, "tags": ("overlay", "selection", "mechanic_preview")}
            if dash is not None:
                kwargs["dash"] = dash
            canvas.create_line(x0 * scale, y0 * scale, x1 * scale, y1 * scale, **kwargs)

        def platform_shuttle_distance(platform) -> float:
            target_speed = abs(int(platform.extra.get("max_velocity", 1) or 1))
            velocity = abs(int(platform.extra.get("velocity", 0) or 0))
            duration = max(1, int(platform.extra.get("unkA", 48) or 48))
            counter = max(0, int(platform.extra.get("counter", 0) or 0))
            distance = 0
            # Mirrors the runtime update: velocity eases by 1 px/tick toward
            # max_velocity, and the reversal counter advances only at max speed.
            for _ in range(4096):
                if velocity < target_speed:
                    velocity += 1
                elif velocity > target_speed:
                    velocity -= 1
                distance += velocity
                if velocity == target_speed:
                    counter += 1
                    if counter >= duration:
                        break
            return float(max(16, min(1024, distance)))

        def platform_direction(platform) -> tuple[float, float, float, str]:
            if platform.platform_type == 8:
                drop = max(32, min(192, int(platform.extra.get("y_delta", 96) or 96)))
                return 0.0, 1.0, float(drop), "falls when ridden"
            directions = {
                0: (0.0, -1.0),
                1: (1.0, -1.0),
                2: (1.0, 0.0),
                3: (1.0, 1.0),
                4: (0.0, 1.0),
                5: (-1.0, 1.0),
                6: (-1.0, 0.0),
                7: (-1.0, -1.0),
            }
            dx, dy = directions.get(platform.platform_type & 7, (0.0, 0.0))
            return dx, dy, platform_shuttle_distance(platform), "estimated travel to turn-around"

        def draw_platform_mechanic_preview(platform, index: int) -> None:
            dx, dy, distance, label = platform_direction(platform)
            if dx == 0 and dy == 0:
                return
            start_x = float(platform.x_pos)
            start_y = float(platform.y_pos)
            end_x = start_x + dx * distance
            end_y = start_y + dy * distance
            preview_color = "#7CFFB2"
            arrow_world(start_x, start_y, end_x, end_y, fill=preview_color, width=4)
            if platform.platform_type != 8:
                arrow_world(end_x, end_y, start_x, start_y, fill=preview_color, width=2, dash=(5, 4))
            canvas.create_oval(
                end_x * scale - 5,
                end_y * scale - 5,
                end_x * scale + 5,
                end_y * scale + 5,
                outline=preview_color,
                width=2,
                tags=("overlay", "selection", "mechanic_preview"),
            )

        def draw_gate_mechanic_preview(gate, index: int) -> None:
            sx, sy = self.level.tilemap_xy(gate.enter_pos)
            dx, dy = self.level.tilemap_xy(gate.dst_pos)
            start_x = sx * 16 + 8
            start_y = sy * 16 + 8
            end_x = dx * 16 + 8
            end_y = dy * 16 + 8
            preview_color = "#7CFFB2"
            arrow_world(start_x, start_y, end_x, end_y, fill=preview_color, width=4)
            canvas.create_text(
                end_x * scale + 8,
                end_y * scale - 10,
                text=f"G{index}: destination",
                fill=preview_color,
                anchor="nw",
                tags=("overlay", "selection", "mechanic_preview"),
            )
            canvas.create_text(
                end_x * scale + 8,
                end_y * scale - 10,
                text=f"P{index}: {label}",
                fill=preview_color,
                anchor="nw",
                tags=("overlay", "selection", "mechanic_preview"),
            )

        def tile_rect(pos: int, label: str, *, outline: str, fill: str) -> None:
            tx, ty = self.level.tilemap_xy(pos)
            x0, y0 = tx * 16, ty * 16
            rect_world(x0, y0, x0 + 16, y0 + 16, outline=outline, fill=fill, width=1, stipple="gray25")
            text_world(x0 + 2, y0 + 2, label)

        def line_world(points: list[tuple[float, float]], *, fill: str, width: int = 2, dash=None, tags: tuple[str, ...] = ("overlay", "physics_overlay")) -> None:
            if len(points) < 2:
                return
            flat: list[float] = []
            for x, y in points:
                flat.extend([x * scale, y * scale])
            kwargs = {"fill": fill, "width": width, "tags": tags, "capstyle": tk.ROUND, "joinstyle": tk.ROUND}
            if dash is not None:
                kwargs["dash"] = dash
            canvas.create_line(*flat, **kwargs)

        def polygon_world(points: list[tuple[float, float]], *, outline: str = "", fill: str = "", stipple: str | None = None, width: int = 1, tags: tuple[str, ...] = ("overlay", "physics_overlay")) -> None:
            if len(points) < 3:
                return
            flat: list[float] = []
            for x, y in points:
                flat.extend([x * scale, y * scale])
            kwargs = {"outline": outline, "fill": fill, "width": width, "tags": tags}
            if stipple:
                kwargs["stipple"] = stipple
            canvas.create_polygon(*flat, **kwargs)

        def floor_profile_points(tile_x: int, tile_y: int, attr3: int) -> list[tuple[float, float]]:
            x0 = tile_x * 16
            y0 = tile_y * 16
            base = attr3 & 0x0F
            slope = attr3 & 0x30
            pts: list[tuple[float, float]] = []
            for lx in range(0, 16):
                if slope == 0x10:
                    ly = base + (lx // 3)
                elif slope == 0x20:
                    ly = base + ((15 - lx) // 3)
                else:
                    ly = base
                ly = max(0, min(15, ly))
                pts.append((x0 + lx + 0.5, y0 + ly + 0.5))
            return pts

        def zigzag_edge_world(x: float, y0: float, y1: float, *, fill: str, teeth: int = 4, inward: float = 3.0) -> None:
            pts: list[tuple[float, float]] = [(x, y0)]
            span = max(1.0, y1 - y0)
            for i in range(1, teeth * 2):
                y = y0 + span * i / (teeth * 2)
                px = x + inward if i % 2 else x
                pts.append((px, y))
            pts.append((x, y1))
            line_world(pts, fill=fill, width=2)

        def draw_surface_hatching(surface: list[tuple[float, float]], *, color: str, density: int) -> None:
            if not surface:
                return
            step = max(3, 9 - density)
            for i in range(2, len(surface) - 1, step):
                x, y = surface[i]
                line_world([(x - 2.0, y + 3.0), (x + 2.0, y - 1.0)], fill=color, width=1)

        def draw_drop_through_arrows(surface: list[tuple[float, float]], *, color: str) -> None:
            if not surface:
                return
            for i in range(4, len(surface), 7):
                x, y = surface[i]
                line_world([(x, y + 1.0), (x, y + 5.0)], fill=color, width=1)
                line_world([(x - 1.8, y + 3.3), (x, y + 5.0), (x + 1.8, y + 3.3)], fill=color, width=1)

        def draw_top_hazard_ticks(surface: list[tuple[float, float]], *, color: str) -> None:
            if not surface:
                return
            for i in range(3, len(surface) - 1, 5):
                x, y = surface[i]
                line_world([(x - 2.0, y), (x, y - 3.0), (x + 2.0, y)], fill=color, width=1)

        def draw_physics_tile(tile_x: int, tile_y: int, tile_num: int) -> None:
            attr0 = self.level.tile_attributes0[tile_num]
            attr1 = self.level.tile_attributes1[tile_num]
            attr2 = self.level.tile_attributes2[tile_num]
            attr3 = self.level.tile_attributes3[tile_num]
            underside = attr2 & 0x0F
            if attr0 == 0 and attr1 == 0 and underside == 0 and attr3 == 0:
                return

            solid_color = "#46E58C"
            slippery_color = "#78E8FF"
            drop_color = "#FFD65A"
            deadly_color = "#FF5B5B"

            x0 = tile_x * 16
            y0 = tile_y * 16
            x1 = x0 + 16
            y1 = y0 + 16
            surface = floor_profile_points(tile_x, tile_y, attr3)

            rect_world(x0, y0, x1, y1, outline="#606060", fill="", width=1)

            if attr1 != 0:
                top_color = {
                    1: solid_color,
                    2: slippery_color,
                    3: slippery_color,
                    4: slippery_color,
                    5: drop_color,
                    6: deadly_color,
                }.get(attr1, "#FFFFFF")
                if attr1 == 5:
                    line_world(surface, fill=top_color, width=2, dash=(4, 2))
                    draw_drop_through_arrows(surface, color=top_color)
                else:
                    line_world(surface, fill=top_color, width=2)
                if attr1 in (2, 3, 4):
                    draw_surface_hatching(surface, color=top_color, density=attr1)
                elif attr1 == 6:
                    draw_top_hazard_ticks(surface, color=top_color)
            elif attr3 != 0:
                line_world(surface, fill="#B6F0B6", width=1, dash=(2, 2))

            slope_bits = attr3 & 0x30
            base = attr3 & 0x0F
            if slope_bits == 0x10:
                line_world([(x0 + 3.0, y0 + base + 1.0), (x0 + 13.0, y0 + min(15, base + 4.0))], fill="#FFFFFF", width=1)
            elif slope_bits == 0x20:
                line_world([(x0 + 3.0, y0 + min(15, base + 4.0)), (x0 + 13.0, y0 + base + 1.0)], fill="#FFFFFF", width=1)

            if attr0 == 1:
                line_world([(x0 + 1.5, y0 + 1.5), (x0 + 1.5, y1 - 1.5)], fill=solid_color, width=2)
                line_world([(x1 - 1.5, y0 + 1.5), (x1 - 1.5, y1 - 1.5)], fill=solid_color, width=2)
            elif attr0 == 2:
                zigzag_edge_world(x0 + 1.5, y0 + 1.5, y1 - 1.5, fill=deadly_color, inward=3.0)
                zigzag_edge_world(x1 - 1.5, y0 + 1.5, y1 - 1.5, fill=deadly_color, inward=-3.0)

            if underside == 1:
                line_world([(x0 + 1.5, y1 - 1.5), (x1 - 1.5, y1 - 1.5)], fill=solid_color, width=2)
            elif underside == 2:
                bottom = [(x0 + 1.5, y1 - 1.5)]
                teeth = 5
                span = x1 - x0 - 3.0
                for i in range(1, teeth * 2):
                    x = x0 + 1.5 + span * i / (teeth * 2)
                    y = y1 - 4.5 if i % 2 else y1 - 1.5
                    bottom.append((x, y))
                bottom.append((x1 - 1.5, y1 - 1.5))
                line_world(bottom, fill=deadly_color, width=2)

        def draw_sprite(sprite_num: int | None, world_x: int, world_y: int, *, top_left: bool = False, centered: bool = False) -> None:
            if sprite_num is None or not self.sprites_blob:
                return
            if not 0 <= sprite_num < self.sprite_tables.count:
                return
            try:
                sprite = render_sprite_image(
                    self.sprites_blob,
                    self.sprite_tables,
                    self.palettes[self.current_level_index],
                    sprite_num,
                    scale=scale,
                )
            except Exception:
                return
            if centered:
                x = world_x * scale - sprite.width // 2
                y = world_y * scale - sprite.height // 2
            elif top_left:
                x = world_x * scale
                y = world_y * scale
            else:
                origin_x, _origin_y = self.sprite_tables.origin(sprite_num)
                _w, h = self.sprite_tables.size(sprite_num)
                x = (world_x - origin_x) * scale
                y = (world_y - h) * scale
            photo = ImageTk.PhotoImage(sprite)
            self._level_overlay_photos.append(photo)
            canvas.create_image(x, y, image=photo, anchor="nw", tags=("overlay", "sprite_overlay"))

        flags = self._active_tile_overlay_flags()
        if flags:
            for ty in range(self.level.height_tiles):
                row_off = ty * self.level.width_tiles
                for tx in range(self.level.width_tiles):
                    tile_num = self.level.tilemap[row_off + tx]
                    x0, y0 = tx * 16, ty * 16
                    if "front" in flags and self.level.tile_attributes2[tile_num] & 0x40:
                        rect_world(x0, y0, x0 + 16, y0 + 16, outline="#FFFFFF", fill="#FFFFFF", stipple="gray25")
                    if "animated" in flags and self.level.is_animated_tile_member(tile_num):
                        rect_world(x0, y0, x0 + 16, y0 + 16, outline="#66FFFF", fill="#66FFFF", stipple="gray25")
                    if "solid_attr1" in flags and self.level.tile_attributes1[tile_num] != 0:
                        rect_world(x0, y0, x0 + 16, y0 + 16, outline="#FFB347", fill="#FFB347", stipple="gray25")
                    if "special_attr0" in flags and self.level.tile_attributes0[tile_num] != 0:
                        rect_world(x0, y0, x0 + 16, y0 + 16, outline="#CC99FF", fill="#CC99FF", stipple="gray25")
                    if "shape_attr3" in flags and self.level.tile_attributes3[tile_num] != 0:
                        rect_world(x0, y0, x0 + 16, y0 + 16, outline="#90EE90", fill="#90EE90", stipple="gray25")
                    if "physics_diagram" in flags:
                        draw_physics_tile(tx, ty, tile_num)

        obj_flags = self._active_object_overlay_flags()
        if "player_start" in obj_flags:
            sx = self.level.header.start_x_pos
            sy = self.level.header.start_y_pos
            px = sx * scale
            py = sy * scale
            canvas.create_line(px - 12, py, px + 12, py, fill="#00FFB3", width=2, tags=("overlay", "player_start"))
            canvas.create_line(px, py - 12, px, py + 12, fill="#00FFB3", width=2, tags=("overlay", "player_start"))
            canvas.create_oval(px - 7, py - 7, px + 7, py + 7, outline="#00FFB3", width=2, tags=("overlay", "player_start"))
            canvas.create_text(px + 12, py - 12, text="START", fill="#00FFB3", anchor="nw", tags=("overlay", "player_start"))
        if "items" in obj_flags:
            for index, item in enumerate(self.level.items):
                if not item.active:
                    continue
                spr = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
                draw_sprite(spr, item.x_pos, item.y_pos)
                marker_world(item.x_pos, item.y_pos, f"I{index}", color="#32DC78")

        if "platforms" in obj_flags:
            for index, platform in enumerate(self.level.platforms):
                if not platform.active:
                    continue
                spr = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
                draw_sprite(spr, platform.x_pos, platform.y_pos)
                marker_world(platform.x_pos, platform.y_pos, f"P{index}", color="#FF8C46")

        if "monsters" in obj_flags:
            expert = self.difficulty.get() == "Expert"
            for index, monster in enumerate(self.level.monsters):
                if not expert and monster.expert_only:
                    continue
                spr = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
                if monster.uses_trigger_rect:
                    rect = monster.trigger_rect_tiles
                    if rect is None:
                        continue
                    tx, ty, tw, th = rect
                    x0, y0 = tx * 16, ty * 16
                    x1 = (tx + tw + 1) * 16
                    y1 = (ty + th + 1) * 16
                    rect_world(x0, y0, x1, y1, outline="#FF3C3C", fill="#FF3C3C", width=2, stipple="gray25")
                    text_world(x0 + 2, y0 + 2, f"M{index}: trigger T{monster.movement_type}")
                    draw_sprite(spr, (x0 + x1) // 2, (y0 + y1) // 2, centered=True)
                else:
                    draw_sprite(spr, monster.x_pos, monster.y_pos)
                    marker_world(monster.x_pos, monster.y_pos, f"M{index}:T{monster.movement_type}", color="#FF3C3C")

        if "gates" in obj_flags:
            for index, gate in enumerate(self.level.gates):
                if not gate.active:
                    continue
                tile_rect(gate.enter_pos, f"G{index}", outline="#50B4FF", fill="#50B4FF")
                tile_rect(gate.dst_pos, f"D{index}", outline="#50B4FF", fill="#50B4FF")

        if "bonuses" in obj_flags:
            secret_colors = {
                "small_random_bonus": "#FFD73C",
                "tile_reveal": "#6CCBFF",
                "big_random_bonus": "#FF6B6B",
            }
            for slot_index, bonus in enumerate(self.level.bonuses):
                if not bonus.active:
                    continue
                color = secret_colors.get(bonus.mode, "#FFFFFF")
                tile_rect(bonus.pos, f"S{slot_index}", outline=color, fill=color)

        if "columns" in obj_flags:
            for index, column in enumerate(self.level.columns):
                if not column.active:
                    continue
                tx, ty = self.level.tilemap_xy(column.tilemap_pos)
                x0, y0 = tx * 16, ty * 16
                x1 = x0 + max(1, column.width) * 16
                y1 = y0 + max(1, column.height) * 16
                rect_world(x0, y0, x1, y1, outline="#B464FF", fill="#B464FF", width=2, stipple="gray25")
                text_world(x0 + 2, y0 + 2, f"C{index}")
                if column.trigger_pos not in (0xFFFF, 0xFFFE):
                    tile_rect(column.trigger_pos, f"CT{index}", outline="#B464FF", fill="#B464FF")

        if "boss" in obj_flags and self.level.boss.active:
            boss = self.level.boss
            marker_world(boss.x_pos, boss.y_pos, "BOSS", color="#FF50D2")
            canvas.create_line(boss.x_min * scale, boss.y_pos * scale, boss.x_max * scale, boss.y_pos * scale, fill="#FF50D2", width=2, tags=("overlay",))
            text_world(boss.x_min, max(0, boss.y_pos - 16), "boss x-range")

        if self.selected_parsed_object is not None:
            selected_kind, selected_index = self.selected_parsed_object
            selection_color = "#FFF06A"
            if selected_kind == "monster" and 0 <= selected_index < len(self.level.monsters):
                monster = self.level.monsters[selected_index]
                if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                    tx, ty, tw, th = monster.trigger_rect_tiles
                    x0, y0 = tx * 16, ty * 16
                    x1, y1 = (tx + tw + 1) * 16, (ty + th + 1) * 16
                    rect_world(x0, y0, x1, y1, outline=selection_color, fill="", width=3)
                    text_world(x0 + 2, max(0, y0 - 12), f"Selected M{selected_index}", fill=selection_color)
                    for corner_x, corner_y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                        px, py = corner_x * scale, corner_y * scale
                        canvas.create_rectangle(
                            px - 6,
                            py - 6,
                            px + 6,
                            py + 6,
                            fill=selection_color,
                            outline="#202020",
                            width=2,
                            tags=("overlay", "selection", "trigger_resize_handle"),
                        )
                else:
                    px, py = monster.x_pos * scale, monster.y_pos * scale
                    canvas.create_oval(px - 14, py - 14, px + 14, py + 14, outline=selection_color, width=3, tags=("overlay", "selection"))
                    canvas.create_text(px + 16, py - 14, text=f"Selected M{selected_index}", fill=selection_color, anchor="nw", tags=("overlay", "selection"))
            elif selected_kind == "item" and 0 <= selected_index < len(self.level.items):
                item = self.level.items[selected_index]
                px, py = item.x_pos * scale, item.y_pos * scale
                canvas.create_oval(px - 14, py - 14, px + 14, py + 14, outline=selection_color, width=3, tags=("overlay", "selection"))
                canvas.create_text(px + 16, py - 14, text=f"Selected item", fill=selection_color, anchor="nw", tags=("overlay", "selection"))
            elif selected_kind == "platform" and 0 <= selected_index < len(self.level.platforms):
                platform = self.level.platforms[selected_index]
                draw_platform_mechanic_preview(platform, selected_index)
                px, py = platform.x_pos * scale, platform.y_pos * scale
                canvas.create_oval(px - 16, py - 16, px + 16, py + 16, outline=selection_color, width=3, tags=("overlay", "selection"))
                canvas.create_text(px + 18, py - 16, text=f"Selected platform", fill=selection_color, anchor="nw", tags=("overlay", "selection"))
            elif selected_kind == "gate" and 0 <= selected_index < len(self.level.gates):
                gate = self.level.gates[selected_index]
                draw_gate_mechanic_preview(gate, selected_index)
                focus_side = self.gate_focus_side.get(selected_index, "source")
                focus_pos = gate.dst_pos if focus_side == "destination" else gate.enter_pos
                tx, ty = self.level.tilemap_xy(focus_pos)
                rect_world(tx * 16, ty * 16, (tx + 1) * 16, (ty + 1) * 16, outline=selection_color, fill="", width=3)
                text_world(tx * 16 + 2, max(0, ty * 16 - 12), f"Gate {selected_index}: {focus_side}", fill=selection_color)
            elif selected_kind == "column" and 0 <= selected_index < len(self.level.columns):
                column = self.level.columns[selected_index]
                tx, ty = self.level.tilemap_xy(column.tilemap_pos)
                x0, y0 = tx * 16, ty * 16
                x1, y1 = (tx + max(1, column.width)) * 16, (ty + max(1, column.height)) * 16
                rect_world(x0, y0, x1, y1, outline=selection_color, fill="", width=3)
                text_world(x0 + 2, max(0, y0 - 12), f"Selected column {selected_index}", fill=selection_color)
            elif selected_kind == "secret" and 0 <= selected_index < len(self.level.bonuses):
                bonus = self.level.bonuses[selected_index]
                tx, ty = self.level.tilemap_xy(bonus.pos)
                rect_world(tx * 16, ty * 16, (tx + 1) * 16, (ty + 1) * 16, outline=selection_color, fill="", width=3)
                text_world(tx * 16 + 2, max(0, ty * 16 - 12), f"Selected S{selected_index}", fill=selection_color)
            elif selected_kind == "boss" and self.level.boss.active:
                boss = self.level.boss
                px, py = boss.x_pos * scale, boss.y_pos * scale
                canvas.create_oval(px - 18, py - 18, px + 18, py + 18, outline=selection_color, width=3, tags=("overlay", "selection"))
                canvas.create_text(px + 20, py - 18, text="Selected boss", fill=selection_color, anchor="nw", tags=("overlay", "selection"))

        if self.grid_enabled.get():
            for x in range(0, map_w + 1, tile_px):
                canvas.create_line(x, 0, x, map_h, fill="#808080", width=1, tags=("overlay", "grid"))
            for y in range(0, map_h + 1, tile_px):
                canvas.create_line(0, y, map_w, y, fill="#808080", width=1, tags=("overlay", "grid"))

        self._redraw_animated_tile_overlay()

    def _refresh_game_files(self) -> None:
        if not hasattr(self, "files_tree"):
            return
        self.files_tree.delete(*self.files_tree.get_children())
        self.game_file_records.clear()
        if not self.data_path.exists():
            self.files_tree.insert("", "end", values=(str(self.data_path), "", "", "Folder not found"))
            return

        files = sorted((p for p in self.data_path.iterdir() if p.is_file()), key=lambda p: p.name.lower())
        for path in files:
            try:
                raw = path.read_bytes()
                size = f"{len(raw):,} B"
                signature = raw[:4].hex(" ").upper() if raw else "—"
                decoded = "—"
                parser = "raw / not probed"
                decoded_len = ""
                suffix = path.suffix.lower()
                if suffix in {".sqz", ".trk"}:
                    try:
                        unpacked = unpack_file(path)
                        decoded = f"{len(unpacked):,} B"
                        decoded_len = str(len(unpacked))
                        parser = "decompression OK"
                    except Exception as exc:
                        parser = f"decompression failed: {exc}"
                iid = str(path)
                self.game_file_records[iid] = {
                    "name": path.name,
                    "path": str(path),
                    "size": size,
                    "signature": signature,
                    "decoded": decoded,
                    "decoded_len": decoded_len,
                    "parser": parser,
                }
                self.files_tree.insert("", "end", iid=iid, values=(path.name, size, decoded, parser))
            except Exception as exc:
                iid = str(path)
                self.game_file_records[iid] = {
                    "name": path.name,
                    "path": str(path),
                    "size": "",
                    "signature": "",
                    "decoded": "",
                    "decoded_len": "",
                    "parser": f"read failed: {exc}",
                }
                self.files_tree.insert("", "end", iid=iid, values=(path.name, "", "", f"read failed: {exc}"))

    def _set_file_preview_text(self, text: str) -> None:
        self.file_preview_text.configure(state="normal")
        self.file_preview_text.delete("1.0", "end")
        self.file_preview_text.insert("1.0", text)
        self.file_preview_text.configure(state="disabled")

    def _on_game_file_selected(self, _event=None) -> None:
        selection = self.files_tree.selection()
        if not selection:
            return
        iid = selection[0]
        record = self.game_file_records.get(iid)
        if record is None:
            return
        self._set_file_preview_text(
            f"File: {record['name']}\n"
            f"Path: {record['path']}\n"
            f"Raw size: {record['size']}\n"
            f"First bytes: {record['signature']}\n"
            f"Decoded size: {record['decoded']}\n"
            f"Parser: {record['parser']}\n"
        )
        self.file_preview_canvas.clear()
        path = Path(record["path"])
        lower_name = path.name.lower()
        if lower_name.startswith("back") and lower_name.endswith(".sqz") and self.level is not None:
            try:
                background_blob = unpack_file(path)
                background = render_background_image(
                    background_blob,
                    self.palettes[self.current_level_index],
                    scale=2,
                )
                self.file_background_photo = ImageTk.PhotoImage(background)
                self.file_preview_canvas.set_image(self.file_background_photo)
                self.file_preview_notebook.select(1)
                self._set_file_preview_text(
                    self.file_preview_text.get("1.0", "end").strip()
                    + "\n\nVisualizer: decoded BACK*.SQZ as a 320×200 planar VGA background using the current level palette."
                )
            except Exception as exc:
                self._set_file_preview_text(
                    self.file_preview_text.get("1.0", "end").strip()
                    + f"\n\nBackground visualizer failed: {exc}"
                )
        elif lower_name == "sprites.sqz" and self.sprites_blob and self.level is not None:
            try:
                atlas = render_sprite_atlas(
                    self.sprites_blob,
                    self.sprite_tables,
                    self.palettes[self.current_level_index],
                    columns=8,
                    cell_w=96,
                    cell_h=96,
                    scale=1,
                )
                self.file_sprite_atlas_photo = ImageTk.PhotoImage(atlas)
                self.file_preview_canvas.set_image(self.file_sprite_atlas_photo)
                self.file_preview_notebook.select(1)
                self._set_file_preview_text(
                    self.file_preview_text.get("1.0", "end").strip()
                    + "\n\nVisualizer: decoded SPRITES.SQZ as a 461-frame atlas using the current level palette."
                )
            except Exception as exc:
                self._set_file_preview_text(
                    self.file_preview_text.get("1.0", "end").strip()
                    + f"\n\nSprite visualizer failed: {exc}"
                )
        else:
            self._set_file_preview_text(
                self.file_preview_text.get("1.0", "end").strip()
                + "\n\nVisualizer: not implemented for this file yet."
            )

    def _clear_parsed_detail_controls(self) -> None:
        if not hasattr(self, "tables_detail_form"):
            return
        self.tables_detail_form.clear()
        self._detail_vars.clear()
        self._last_editor_edit_widget = None
        self._editor_widget_committers.clear()

    def _hold_detail_var(self, var: tk.Variable) -> tk.Variable:
        self._detail_vars.append(var)
        return var

    def _hold_tile_var(self, var: tk.Variable) -> tk.Variable:
        self._tile_property_vars.append(var)
        return var

    def _editor_section(self, parent: tk.Misc, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, padding=6)
        frame.pack(fill="x", padx=2, pady=(0, 6))
        frame.columnconfigure(1, weight=1)
        return frame

    def _editor_note(self, parent: tk.Misc, text: str) -> None:
        """Add a prose note to either a packed container or a grid-based editor section.

        Editor sections place their field rows with ``grid``; top-level detail
        containers stack sections with ``pack``.  Mixing the two geometry
        managers in the same parent triggers TclError, so notes adapt to the
        parent that receives them.
        """
        label = ttk.Label(parent, text=text, wraplength=420, justify="left")
        if parent.grid_slaves():
            rows = [int(info.get("row", 0)) for child in parent.grid_slaves() for info in (child.grid_info(),)]
            row = (max(rows) + 1) if rows else 0
            label.grid(row=row, column=0, columnspan=2, sticky="ew", padx=2, pady=(2, 6))
        else:
            label.pack(fill="x", anchor="w", padx=2, pady=(0, 6))

    def _editor_field_row(self, parent: ttk.LabelFrame, label: str, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)

    def _commit_editor_value(self, callback, raw_value: str, *, var: tk.StringVar | None = None, original: str | None = None) -> None:
        if callback is None:
            return
        try:
            callback(raw_value)
        except Exception as exc:
            if var is not None and original is not None:
                var.set(original)
            self.status_text.set(f"Property edit rejected: {exc}")
            try:
                messagebox.showwarning("Invalid property value", str(exc))
            except Exception:
                pass

    def _remember_editor_edit_widget(self, widget: tk.Widget) -> None:
        self._last_editor_edit_widget = widget

    def _commit_last_editor_edit_widget(self) -> None:
        widget = self._last_editor_edit_widget
        if widget is None:
            return
        try:
            exists = bool(widget.winfo_exists())
        except Exception:
            exists = False
        if not exists:
            self._last_editor_edit_widget = None
            return
        commit = self._editor_widget_committers.get(widget)
        if commit is not None:
            prior = self._suspend_editor_detail_refresh
            self._suspend_editor_detail_refresh = True
            try:
                commit()
            finally:
                self._suspend_editor_detail_refresh = prior

    def _commit_all_editor_edit_widgets(self) -> None:
        """Flush every visible hand-typed Entry/Spinbox before Apply.

        ttk does not guarantee that clicking Apply delivers FocusOut before the
        button command.  Worse, our staging callbacks may rebuild the inspector,
        which can destroy the button during that focus transition.  Apply calls
        this deterministic batch flush while detail refreshes are suspended.
        """
        committers = list(self._editor_widget_committers.items())
        prior = self._suspend_editor_detail_refresh
        self._suspend_editor_detail_refresh = True
        try:
            for widget, commit in committers:
                try:
                    if widget.winfo_exists():
                        commit()
                except Exception:
                    # Individual committers already surface validation errors;
                    # continue so another edited field is not silently skipped.
                    continue
        finally:
            self._suspend_editor_detail_refresh = prior

    def _add_editor_entry(
        self,
        parent: ttk.LabelFrame,
        label: str,
        value: object,
        row: int,
        *,
        kind: str = "detail",
        width: int = 24,
        editable: bool = False,
        on_commit=None,
    ) -> None:
        self._editor_field_row(parent, label, row)
        initial = str(value)
        var = tk.StringVar(value=initial)
        if kind == "tile":
            self._hold_tile_var(var)
        else:
            self._hold_detail_var(var)
        entry = ttk.Entry(parent, textvariable=var, width=width, state="normal" if editable and on_commit else "readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=2)
        if editable and on_commit:
            def commit(_event=None, v=var, original=initial, callback=on_commit):
                self._commit_editor_value(callback, v.get(), var=v, original=original)
            self._editor_widget_committers[entry] = commit
            entry.bind("<FocusIn>", lambda _event, w=entry: self._remember_editor_edit_widget(w))
            entry.bind("<KeyRelease>", lambda _event, w=entry: self._remember_editor_edit_widget(w), add="+")
            entry.bind("<Return>", commit)

    def _add_editor_spinbox(
        self,
        parent: ttk.LabelFrame,
        label: str,
        value: object,
        row: int,
        *,
        kind: str = "detail",
        from_: int = -32768,
        to: int = 32767,
        editable: bool = False,
        on_commit=None,
    ) -> None:
        self._editor_field_row(parent, label, row)
        initial = str(value)
        var = tk.StringVar(value=initial)
        if kind == "tile":
            self._hold_tile_var(var)
        else:
            self._hold_detail_var(var)
        spin = ttk.Spinbox(parent, from_=from_, to=to, textvariable=var, state="normal" if editable and on_commit else "readonly", width=16)
        spin.grid(row=row, column=1, sticky="ew", pady=2)
        if editable and on_commit:
            def commit(_event=None, v=var, original=initial, callback=on_commit):
                self._commit_editor_value(callback, v.get(), var=v, original=original)
            self._editor_widget_committers[spin] = commit
            spin.bind("<FocusIn>", lambda _event, w=spin: self._remember_editor_edit_widget(w))
            spin.bind("<KeyRelease>", lambda _event, w=spin: self._remember_editor_edit_widget(w), add="+")
            spin.configure(command=commit)
            spin.bind("<Return>", commit)
        else:
            spin.bind("<ButtonPress-1>", lambda _event: "break")
            spin.bind("<KeyPress>", lambda _event: "break")

    def _add_editor_combo(
        self,
        parent: ttk.LabelFrame,
        label: str,
        value: object,
        row: int,
        *,
        kind: str = "detail",
        values: list[str] | tuple[str, ...] | None = None,
        editable: bool = False,
        on_select=None,
    ) -> None:
        self._editor_field_row(parent, label, row)
        initial = str(value)
        var = tk.StringVar(value=initial)
        if kind == "tile":
            self._hold_tile_var(var)
        else:
            self._hold_detail_var(var)
        combo_values = [str(item) for item in (values if values is not None else [value])]
        combo = ttk.Combobox(parent, textvariable=var, values=combo_values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=2)
        if editable and on_select:
            combo.bind("<<ComboboxSelected>>", lambda _event, v=var, callback=on_select, original=initial: self._commit_editor_value(callback, v.get(), var=v, original=original))
        else:
            combo.bind("<<ComboboxSelected>>", lambda _event, v=var, original=initial: v.set(original))

    def _next_editor_grid_row(self, parent: tk.Misc) -> int:
        """Return the next free grid row in an editor section.

        Editor sections use `grid` internally for fields.  Checkbox rows must
        therefore also occupy a grid row in the section itself; their *inside*
        layout may use pack safely because that happens in a nested frame.
        """
        rows: list[int] = []
        for child in parent.grid_slaves():
            info = child.grid_info()
            try:
                rows.append(int(info.get("row", 0)))
            except Exception:
                rows.append(0)
        return (max(rows) + 1) if rows else 0

    def _add_readonly_checkbox(
        self,
        parent: tk.Misc,
        label: str,
        value: bool,
        *,
        future_editable: bool,
        note: str | None = None,
        kind: str = "detail",
        on_toggle=None,
    ) -> None:
        row_index = self._next_editor_grid_row(parent)
        row = ttk.Frame(parent)
        row.grid(row=row_index, column=0, columnspan=2, sticky="ew", pady=1)
        try:
            row.columnconfigure(1, weight=1)
        except Exception:
            pass
        var = tk.BooleanVar(value=bool(value))
        if kind == "tile":
            self._hold_tile_var(var)
        else:
            self._hold_detail_var(var)
        checkbox = ttk.Checkbutton(row, text=label, variable=var, takefocus=False)
        checkbox.pack(side="left", anchor="w")
        if on_toggle is not None:
            checkbox.configure(command=lambda v=var, callback=on_toggle: self._commit_editor_value(callback, "1" if v.get() else "0"))
        elif future_editable:
            checkbox.bind("<ButtonPress-1>", lambda _event: "break")
            checkbox.bind("<ButtonRelease-1>", lambda _event: "break")
            checkbox.bind("<space>", lambda _event: "break")
            checkbox.bind("<Return>", lambda _event: "break")
        else:
            checkbox.state(["disabled"])
        if note:
            note_label = ttk.Label(row, text=note, wraplength=340, justify="left")
            note_label.pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _monster_extra_label(self, key: str) -> str:
        labels = {
            "vertical_range_px": "Vertical travel range",
            "vertical_speed_step": "Vertical speed step",
            "activation_x_range_tiles": "Activation X range",
            "activation_y_range_tiles": "Activation Y range",
            "swing_radius_px": "Swing radius",
            "swing_angle_limit": "Swing angle limit",
            "dash_speed_step": "Dash speed step",
            "launch_speed_step": "Launch speed step",
            "jump_up_speed_step": "Jump up speed step",
            "jump_horizontal_speed_step": "Jump horizontal speed step",
            "patrol_left_x_px": "Patrol left bound",
            "patrol_right_x_px": "Patrol right bound",
            "patrol_max_speed_step": "Patrol max speed step",
            "emerge_attack_speed_step": "Emerge attack speed",
            "leap_horizontal_speed_step": "Leap horizontal speed",
            "leap_up_speed_step": "Leap up speed",
            "max_fall_speed_step": "Maximum fall speed",
            "horizontal_speed_step": "Horizontal speed step",
            "movement_pattern_source": "Movement pattern source",
        }
        return labels.get(key, key.replace("_", " ").title())

    def _platform_extra_label(self, platform, key: str) -> str:
        type8_labels = {
            "y_velocity": "Current fall speed",
            "unk8": "Fall/reset tuning byte 1",
            "unk9": "Fall/reset tuning byte 2",
            "state": "Runtime state",
            "y_delta": "Return distance / Y delta",
            "counter": "Runtime counter",
            "padding": "Padding",
        }
        other_labels = {
            "max_velocity": "Max movement speed",
            "padding": "Padding",
            "unk9": "Motion phase / tuning byte",
            "unkA": "Path anchor / tuning word",
            "counter": "Movement counter",
            "velocity": "Current movement speed",
        }
        labels = type8_labels if platform.variant == "type8" else other_labels
        return labels.get(key, key.replace("_", " ").title())

    def _parse_editor_int(self, raw: str, *, minimum: int | None = None, maximum: int | None = None, label: str = "Value") -> int:
        text = str(raw).strip()
        value = int(text, 0)
        if minimum is not None and value < minimum:
            raise ValueError(f"{label} must be at least {minimum}.")
        if maximum is not None and value > maximum:
            raise ValueError(f"{label} must be at most {maximum}.")
        return value

    def _selected_editor_object(self, kind: str, index: int):
        if self.level is None:
            return None
        if kind == "monster" and 0 <= index < len(self.level.monsters):
            return self.level.monsters[index]
        if kind == "item" and 0 <= index < len(self.level.items):
            return self.level.items[index]
        if kind == "platform" and 0 <= index < len(self.level.platforms):
            return self.level.platforms[index]
        if kind == "gate" and 0 <= index < len(self.level.gates):
            return self.level.gates[index]
        if kind == "column" and 0 <= index < len(self.level.columns):
            return self.level.columns[index]
        if kind == "secret" and 0 <= index < len(self.level.bonuses):
            return self.level.bonuses[index]
        if kind == "boss":
            return self.level.boss
        return None

    def _ensure_selected_property_draft(self, kind: str, index: int):
        target = self._selected_editor_object(kind, index)
        if target is None:
            return None
        if (
            self.selected_property_draft is None
            or self.selected_property_draft[0] != kind
            or self.selected_property_draft[1] != index
        ):
            self.selected_property_draft = (kind, index, copy.deepcopy(target))
            self._selected_property_draft_dirty = False
        return self.selected_property_draft[2]

    def _selected_property_draft_for(self, kind: str, index: int):
        return self._ensure_selected_property_draft(kind, index)

    def _apply_selected_property_draft(self) -> None:
        # Flush manually typed Entry/Spinbox text before comparing the draft to
        # the live object.  Arrow clicks already commit immediately, but plain
        # keyboard entry otherwise could be skipped when Apply is clicked.
        self._commit_all_editor_edit_widgets()
        if self.level is None or self.selected_property_draft is None:
            return
        kind, index, draft = self.selected_property_draft
        current = self._selected_editor_object(kind, index)
        if current is None:
            raise ValueError(f"Cannot apply missing {kind} {index}.")
        if draft == current:
            self._selected_property_draft_dirty = False
            self.status_text.set("No staged property changes to apply.")
            self._refresh_parsed_detail(kind, index)
            return
        self._record_undo_state()
        replacement = copy.deepcopy(draft)
        if kind == "monster":
            self.level.monsters[index] = replacement
        elif kind == "item":
            self.level.items[index] = replacement
        elif kind == "platform":
            self.level.platforms[index] = replacement
        elif kind == "gate":
            self.level.gates[index] = replacement
        elif kind == "column":
            self.level.columns[index] = replacement
        elif kind == "secret":
            self.level.bonuses[index] = replacement
        elif kind == "boss":
            self.level.boss = replacement
        else:
            raise ValueError(f"Unsupported editable object kind: {kind}")
        self._selected_property_draft_dirty = False
        self.selected_property_draft = (kind, index, copy.deepcopy(replacement))
        suffix = " Monster records will be compacted/rebuilt on save." if kind == "monster" else ""
        self._mark_dirty(f"Applied staged {kind} {index} properties.{suffix}")
        self._refresh_tables()
        self.selected_parsed_object = (kind, index)
        self._refresh_parsed_detail(kind, index)
        self._redraw_level_overlays()
        self._refresh_info()

    def _discard_selected_property_draft(self) -> None:
        if self.selected_property_draft is None:
            return
        kind, index, _draft = self.selected_property_draft
        target = self._selected_editor_object(kind, index)
        if target is None:
            self.selected_property_draft = None
            self._selected_property_draft_dirty = False
            return
        self.selected_property_draft = (kind, index, copy.deepcopy(target))
        self._selected_property_draft_dirty = False
        self.status_text.set(f"Discarded staged {kind} {index} property changes.")
        self._refresh_parsed_detail(kind, index)

    def _add_selected_property_apply_bar(self, parent: tk.Misc, kind: str, index: int) -> None:
        bar = ttk.LabelFrame(parent, text="Staged object edit", padding=6)
        bar.pack(fill="x", padx=2, pady=(0, 6))
        status = "Changes are waiting for Apply." if self._selected_property_draft_dirty else "Fields edit a draft; Apply writes one undoable change."
        ttk.Label(bar, text=status, wraplength=420, justify="left").pack(fill="x", anchor="w", pady=(0, 5))
        buttons = ttk.Frame(bar)
        buttons.pack(fill="x")
        apply_button = ttk.Button(buttons, text="Apply", command=self._apply_selected_property_draft)
        apply_button.bind("<ButtonPress-1>", lambda _event: self._commit_all_editor_edit_widgets(), add="+")
        apply_button.pack(side="left", fill="x", expand=True)
        reset_button = ttk.Button(buttons, text="Discard", command=self._discard_selected_property_draft)
        reset_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
        if kind in {"item", "monster", "platform", "gate"}:
            save_template = ttk.Button(
                bar,
                text="Save selected as template…",
                command=self._save_selected_object_as_template,
            )
            save_template.pack(fill="x", pady=(6, 0))

    def _apply_selected_object_mutation(self, kind: str, index: int, label: str, mutator) -> None:
        # Inspector callbacks stage into a private draft.  Nothing in the level
        # model changes until the explicit Apply button is pressed.
        target = self._selected_property_draft_for(kind, index)
        if target is None:
            raise ValueError(f"Cannot edit missing {kind} {index}.")
        before = copy.deepcopy(target)
        mutator(target)
        if target == before:
            return
        self._selected_property_draft_dirty = True
        self.status_text.set(f"Staged: {label} Press Apply to write it.")
        self.selected_parsed_object = (kind, index)
        if not self._suspend_editor_detail_refresh:
            self._refresh_parsed_detail(kind, index)

    def _edit_selected_int_attr(self, kind: str, index: int, attr: str, raw: str, *, minimum: int, maximum: int, label: str) -> None:
        value = self._parse_editor_int(raw, minimum=minimum, maximum=maximum, label=label)
        self._apply_selected_object_mutation(kind, index, f"Changed {kind} {index} {label}.", lambda obj: setattr(obj, attr, value))

    def _edit_selected_extra_int(self, kind: str, index: int, key: str, raw: str, *, minimum: int, maximum: int, label: str) -> None:
        value = self._parse_editor_int(raw, minimum=minimum, maximum=maximum, label=label)
        self._apply_selected_object_mutation(kind, index, f"Changed {kind} {index} {label}.", lambda obj: obj.extra.__setitem__(key, value))

    def _set_monster_expert_flag(self, index: int, raw: str) -> None:
        enabled = raw == "1"
        def mutate(monster):
            monster.type_byte = (monster.type_byte | 0x80) if enabled else (monster.type_byte & 0x7F)
        self._apply_selected_object_mutation("monster", index, f"Changed monster {index} expert-only flag.", mutate)

    def _set_monster_patrol_ground_flag(self, index: int, raw: str) -> None:
        enabled = raw == "1"
        def mutate(monster):
            monster.flags = (monster.flags | 0x08) if enabled else (monster.flags & ~0x08)
        self._apply_selected_object_mutation("monster", index, f"Changed monster {index} terrain patrol flag.", mutate)

    def _monster_visual_choices(self, movement_type: int | None = None) -> tuple[list[str], dict[str, int]]:
        labels: list[str] = []
        mapping: dict[str, int] = {}
        for runtime_sprite in MONSTER_PLACEMENT_VISUALS:
            label = f"{monster_visual_name(runtime_sprite, movement_type)} [sprite {runtime_sprite}]"
            # A few visuals intentionally share a human label; include the global
            # frame in every label so the selection is always unambiguous.
            labels.append(label)
            mapping[label] = int(runtime_sprite)
        return labels, mapping

    def _monster_visual_label_for_runtime(self, runtime_sprite: int | None, movement_type: int | None) -> str:
        labels, mapping = self._monster_visual_choices(movement_type)
        for label in labels:
            if mapping[label] == runtime_sprite:
                return label
        if runtime_sprite is None:
            return "Unknown enemy [sprite ?]"
        return f"{monster_visual_name(runtime_sprite, movement_type)} [sprite {runtime_sprite}]"

    def _set_monster_visual(self, index: int, label: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.monsters):
            raise ValueError("Monster no longer exists.")
        monster = self._selected_property_draft_for("monster", index)
        _labels, mapping = self._monster_visual_choices(monster.movement_type)
        if label not in mapping:
            raise ValueError("Unknown monster visual.")
        runtime_sprite = mapping[label]
        raw_sprite = self.sprite_resolver.monster_raw_for_runtime(self.level, runtime_sprite)
        self._apply_selected_object_mutation(
            "monster",
            index,
            f"Changed monster {index} visual to sprite {runtime_sprite}.",
            lambda obj: setattr(obj, "sprite_num_raw", raw_sprite),
        )

    def _set_draft_monster_visual(self, label: str) -> None:
        if self.level is None:
            raise ValueError("Load a level first.")
        draft = self._draft_object("monster")
        if draft is None:
            raise ValueError("Choose a monster placement draft first.")
        _labels, mapping = self._monster_visual_choices(draft.movement_type)
        if label not in mapping:
            raise ValueError("Unknown monster visual.")
        runtime_sprite = mapping[label]
        raw_sprite = self.sprite_resolver.monster_raw_for_runtime(self.level, runtime_sprite)
        self._apply_draft_mutation(
            "monster",
            f"Placement monster visual changed to sprite {runtime_sprite}.",
            lambda obj: setattr(obj, "sprite_num_raw", raw_sprite),
        )

    def _item_visual_choices(self) -> tuple[list[str], dict[str, int]]:
        labels: list[str] = []
        mapping: dict[str, int] = {}
        for runtime_sprite in sorted(ITEM_VISUAL_NAMES):
            label = f"{item_visual_name(runtime_sprite)} [sprite {runtime_sprite}]"
            labels.append(label)
            mapping[label] = int(runtime_sprite)
        return labels, mapping

    def _item_visual_label_for_runtime(self, runtime_sprite: int | None) -> str:
        labels, mapping = self._item_visual_choices()
        for label in labels:
            if mapping[label] == runtime_sprite:
                return label
        if runtime_sprite is None:
            return "Unknown item [sprite ?]"
        return f"{item_visual_name(runtime_sprite)} [sprite {runtime_sprite}]"

    def _set_item_visual(self, index: int, label: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.items):
            raise ValueError("Item no longer exists.")
        _labels, mapping = self._item_visual_choices()
        if label not in mapping:
            raise ValueError("Unknown item visual.")
        runtime_sprite = mapping[label]
        raw_sprite = self.sprite_resolver.item_raw_for_runtime(self.level, runtime_sprite)
        self._apply_selected_object_mutation(
            "item",
            index,
            f"Changed item {index} visual to sprite {runtime_sprite}.",
            lambda obj: setattr(obj, "sprite_num_raw", raw_sprite),
        )

    def _set_draft_item_visual(self, label: str) -> None:
        if self.level is None:
            raise ValueError("Load a level first.")
        _labels, mapping = self._item_visual_choices()
        if label not in mapping:
            raise ValueError("Unknown item visual.")
        runtime_sprite = mapping[label]
        raw_sprite = self.sprite_resolver.item_raw_for_runtime(self.level, runtime_sprite)
        self._apply_draft_mutation(
            "item",
            f"Placement item visual changed to sprite {runtime_sprite}.",
            lambda obj: setattr(obj, "sprite_num_raw", raw_sprite),
        )

    def _platform_visual_choices(self) -> tuple[list[str], dict[str, int]]:
        labels: list[str] = []
        mapping: dict[str, int] = {}
        for runtime_sprite in sorted(PLATFORM_VISUAL_NAMES):
            label = f"{platform_visual_name(runtime_sprite)} [sprite {runtime_sprite}]"
            labels.append(label)
            mapping[label] = int(runtime_sprite)
        return labels, mapping

    def _platform_visual_label_for_runtime(self, runtime_sprite: int | None) -> str:
        labels, mapping = self._platform_visual_choices()
        for label in labels:
            if mapping[label] == runtime_sprite:
                return label
        if runtime_sprite is None:
            return "Unknown platform [sprite ?]"
        return f"{platform_visual_name(runtime_sprite)} [sprite {runtime_sprite}]"

    def _set_platform_visual(self, index: int, label: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.platforms):
            raise ValueError("Platform no longer exists.")
        _labels, mapping = self._platform_visual_choices()
        if label not in mapping:
            raise ValueError("Unknown platform visual.")
        runtime_sprite = mapping[label]
        raw_sprite = self.sprite_resolver.platform_raw_for_runtime(self.level, runtime_sprite)
        self._apply_selected_object_mutation(
            "platform",
            index,
            f"Changed platform {index} visual to sprite {runtime_sprite}.",
            lambda obj: setattr(obj, "sprite_num_raw", raw_sprite),
        )

    def _set_draft_platform_visual(self, label: str) -> None:
        if self.level is None:
            raise ValueError("Load a level first.")
        _labels, mapping = self._platform_visual_choices()
        if label not in mapping:
            raise ValueError("Unknown platform visual.")
        runtime_sprite = mapping[label]
        raw_sprite = self.sprite_resolver.platform_raw_for_runtime(self.level, runtime_sprite)
        self._apply_draft_mutation(
            "platform",
            f"Placement platform visual changed to sprite {runtime_sprite}.",
            lambda obj: setattr(obj, "sprite_num_raw", raw_sprite),
        )

    def _monster_behavior_choices(self, monster=None) -> tuple[list[str], dict[str, int]]:
        # Apply now rebuilds the fixed monster attr table on serialization, so
        # the inspector may convert between any known behavior record lengths.
        labels: list[str] = []
        mapping: dict[str, int] = {}
        for movement_type in sorted(EXPECTED_MONSTER_LENGTHS):
            label = {
                0: "triggered fly-in spawner",
                1: "static / idle hazard",
                2: "vertical hanging oscillator",
                3: "proximity drop then ground charge",
                4: "swinging / rotating hanging enemy",
                5: "aimed dash when player is nearby",
                6: "scripted follower pattern",
                7: "proximity launch / dash",
                8: "periodic jumping enemy",
                9: "horizontal patrol between bounds",
                10: "ground-emerge spawner in trigger region",
                11: "hit-triggered leap attacker",
                12: "delayed horizontal ambusher",
            }.get(movement_type, f"unknown behavior type {movement_type}")
            labels.append(label)
            mapping[label] = movement_type
        return labels, mapping

    def _set_monster_behavior(self, index: int, label: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.monsters):
            raise ValueError("Monster no longer exists.")
        _labels, mapping = self._monster_behavior_choices()
        if label not in mapping:
            raise ValueError("Unknown monster behavior.")
        movement_type = mapping[label]
        def mutate(obj):
            old_extra = dict(obj.extra)
            obj.type_byte = (obj.type_byte & 0x80) | movement_type
            obj.length = EXPECTED_MONSTER_LENGTHS[movement_type]
            new_extra = monster_extra_defaults(movement_type)
            # Keep semantically identical named authored controls where the new
            # behavior has the same field; everything else gets safe defaults.
            for key in list(new_extra):
                if key in old_extra and isinstance(new_extra[key], type(old_extra[key])):
                    new_extra[key] = old_extra[key]
            obj.extra = new_extra
        self._apply_selected_object_mutation("monster", index, f"Changed monster {index} behavior to {label}.", mutate)

    def _set_monster_trigger_field(self, index: int, field: str, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.monsters):
            raise ValueError("Monster no longer exists.")
        monster = self._selected_property_draft_for("monster", index)
        trigger = monster.trigger_rect_tiles
        if trigger is None:
            raise ValueError("This monster does not use a trigger rectangle.")
        tx, ty, tw, th = trigger
        if field in {"width", "height"}:
            value = self._parse_editor_int(raw, minimum=1, maximum=256, label=f"Trigger {field}") - 1
        else:
            value = self._parse_editor_int(raw, minimum=0, maximum=255, label=f"Trigger {field}")
        values = {"x": tx, "y": ty, "width": tw, "height": th}
        values[field] = value
        def mutate(obj):
            obj.x_pos = (values["y"] << 8) | values["x"]
            obj.y_pos = (values["height"] << 8) | values["width"]
        self._apply_selected_object_mutation("monster", index, f"Changed monster {index} trigger {field}.", mutate)

    def _platform_behavior_choices(self) -> tuple[list[str], dict[str, int]]:
        labels: list[str] = []
        mapping: dict[str, int] = {}
        for platform_type in range(16):
            label = platform_behavior_name(platform_type)
            if label in mapping:
                label = f"{label} (type {platform_type})"
            labels.append(label)
            mapping[label] = platform_type
        return labels, mapping

    def _platform_default_extra_for_type(self, platform_type: int) -> tuple[str, dict[str, int]]:
        if platform_type == 8:
            return "type8", {
                "y_velocity": 0,
                "unk8": 0,
                "unk9": 0,
                "state": 0,
                "y_delta": 0,
                "counter": 0,
                "padding": 0,
            }
        return "other", {
            "max_velocity": 1,
            "padding": 0,
            "unk9": 0,
            "unkA": 48,
            "counter": 0,
            "velocity": 0,
        }

    def _set_platform_behavior(self, index: int, label: str) -> None:
        labels, mapping = self._platform_behavior_choices()
        if label not in mapping:
            raise ValueError("Unknown platform behavior.")
        platform_type = mapping[label]
        def mutate(platform):
            old_type = platform.platform_type
            platform.flags = (platform.flags & 0xF0) | platform_type
            if (old_type == 8) != (platform_type == 8):
                platform.variant, platform.extra = self._platform_default_extra_for_type(platform_type)
            else:
                platform.variant = "type8" if platform_type == 8 else "other"
        self._apply_selected_object_mutation("platform", index, f"Changed platform {index} behavior to {label}.", mutate)

    def _set_item_active(self, index: int, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.items):
            raise ValueError("Item no longer exists.")
        enabled = raw == "1"
        default_raw = self.sprite_resolver.item_raw_for_runtime(self.level, sorted(ITEM_VISUAL_NAMES)[0])
        def mutate(item):
            if enabled and not item.active:
                item.sprite_num_raw = default_raw
            elif not enabled:
                item.sprite_num_raw = 0xFFFF
        self._apply_selected_object_mutation("item", index, f"Changed item {index} active state.", mutate)

    def _set_platform_active(self, index: int, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.platforms):
            raise ValueError("Platform no longer exists.")
        enabled = raw == "1"
        default_raw = self.sprite_resolver.platform_raw_for_runtime(self.level, sorted(PLATFORM_VISUAL_NAMES)[0])
        def mutate(platform):
            if enabled and not platform.active:
                platform.sprite_num_raw = default_raw
            elif not enabled:
                platform.sprite_num_raw = 0xFFFF
        self._apply_selected_object_mutation("platform", index, f"Changed platform {index} active state.", mutate)

    def _set_boss_active(self, raw: str) -> None:
        enabled = raw == "1"
        def mutate(boss):
            if enabled and not boss.active:
                boss.state = 0
            elif not enabled:
                boss.state = 0xFF
        self._apply_selected_object_mutation("boss", 0, "Changed boss controller active state.", mutate)

    def _set_gate_tile_coord(self, index: int, endpoint: str, axis: str, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.gates):
            raise ValueError("Gate no longer exists.")
        gate = self._selected_property_draft_for("gate", index)
        current_pos = gate.dst_pos if endpoint == "destination" else gate.enter_pos
        tx, ty = self.level.tilemap_xy(current_pos)
        value = self._parse_editor_int(raw, minimum=0, maximum=255, label=f"Gate {endpoint} tile {axis.upper()}")
        if axis == "x":
            tx = value
        else:
            ty = value
        pos = (ty << 8) | tx
        def mutate(obj):
            if endpoint == "destination":
                obj.dst_pos = pos
            else:
                obj.enter_pos = pos
        self._apply_selected_object_mutation("gate", index, f"Changed gate {index} {endpoint} tile {axis.upper()}.", mutate)

    def _set_column_tile_coord(self, index: int, target: str, axis: str, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.columns):
            raise ValueError("Column no longer exists.")
        column = self._selected_property_draft_for("column", index)
        pos = column.trigger_pos if target == "trigger" else column.tilemap_pos
        tx, ty = self.level.tilemap_xy(pos)
        value = self._parse_editor_int(raw, minimum=0, maximum=255, label=f"Column {target} tile {axis.upper()}")
        if axis == "x":
            tx = value
        else:
            ty = value
        new_pos = (ty << 8) | tx
        attr = "trigger_pos" if target == "trigger" else "tilemap_pos"
        self._apply_selected_object_mutation("column", index, f"Changed column {index} {target} tile {axis.upper()}.", lambda obj: setattr(obj, attr, new_pos))

    def _set_secret_tile_coord(self, index: int, axis: str, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.bonuses):
            raise ValueError("Secret no longer exists.")
        bonus = self._selected_property_draft_for("secret", index)
        tx, ty = self.level.tilemap_xy(bonus.pos)
        value = self._parse_editor_int(raw, minimum=0, maximum=255, label=f"Secret tile {axis.upper()}")
        if axis == "x":
            tx = value
        else:
            ty = value
        new_pos = (ty << 8) | tx
        self._apply_selected_object_mutation("secret", index, f"Changed secret {index} tile {axis.upper()}.", lambda obj: setattr(obj, "pos", new_pos))

    def _set_secret_hit_count(self, index: int, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.bonuses):
            raise ValueError("Secret no longer exists.")
        bonus = self._selected_property_draft_for("secret", index)
        hits = self._parse_editor_int(raw, minimum=1, maximum=128, label="Estimated hit count")
        if bonus.count & 0x80:
            count = 0x80 | ((hits - 1) & 0x7F)
        elif bonus.count & 0x40:
            count = 0x40 | ((hits - 1) & 0x3F)
        else:
            count = (hits - 1) & 0x3F
        self._apply_selected_object_mutation("secret", index, f"Changed secret {index} hit count.", lambda obj: setattr(obj, "count", count))

    def _set_secret_tile_value(self, index: int, attr: str, raw: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.bonuses):
            raise ValueError("Secret no longer exists.")
        value = self._parse_editor_int(raw, minimum=0, maximum=255, label="Secret tile ID")
        self._apply_selected_object_mutation(
            "secret",
            index,
            f"Changed secret {index} {attr} tile.",
            lambda obj, name=attr, tile=value: setattr(obj, name, tile),
        )

    def _set_secret_mode(self, index: int, label: str) -> None:
        if self.level is None or not 0 <= index < len(self.level.bonuses):
            raise ValueError("Secret no longer exists.")
        label_to_mode = {
            "Small random bonus secret": "small_random_bonus",
            "Tile reveal / appearing structure secret": "tile_reveal",
            "Big random bonus secret": "big_random_bonus",
        }
        mode = label_to_mode.get(str(label), str(label))
        if mode not in {"small_random_bonus", "tile_reveal", "big_random_bonus"}:
            raise ValueError(f"Unknown secret type: {label}")
        draft = self._selected_property_draft_for("secret", index)
        hits = draft.hit_count_estimate
        mask = {"small_random_bonus": 0x00, "tile_reveal": 0x40, "big_random_bonus": 0x80}[mode]
        max_payload = 0x3F if mode in {"small_random_bonus", "tile_reveal"} else 0x7F
        count = mask | min(max(0, hits - 1), max_payload)
        self._apply_selected_object_mutation(
            "secret",
            index,
            f"Changed secret {index} type.",
            lambda obj, new_count=count: setattr(obj, "count", new_count),
        )

    def _select_secret_for_editing(self, index: int) -> None:
        if self.level is None or not 0 <= index < len(self.level.bonuses):
            return
        self.selected_parsed_object = ("secret", index)
        self._refresh_parsed_detail("secret", index)
        self.status_text.set(f"Editing secret S{index} in the right-hand inspector.")
        self._redraw_level_overlays()

    def _add_secret_to_selected_tile(self, mode: str) -> None:
        if self.level is None or not isinstance(self.selected_tile_context, dict):
            self.status_text.set("Select a map tile before adding a secret.")
            return
        map_xy = self.selected_tile_context.get("map_xy")
        if not isinstance(map_xy, tuple) or len(map_xy) != 2:
            self.status_text.set("A secret can only be attached to a concrete map cell.")
            return
        slot = self._first_inactive_slot(self.level.bonuses)
        if slot is None:
            self.status_text.set("No free secret / bonus slots remain in this level.")
            return
        tx, ty = int(map_xy[0]), int(map_xy[1])
        tile_num = int(self.selected_tile_context.get("tile_num", 0x7E)) & 0xFF
        initial = 0x7E if mode == "tile_reveal" else tile_num
        revealed = tile_num
        count = {"small_random_bonus": 0x00, "tile_reveal": 0x40, "big_random_bonus": 0x80}.get(mode)
        if count is None:
            raise ValueError(f"Unsupported secret mode: {mode}")
        self._record_undo_state()
        bonus = self.level.bonuses[slot]
        bonus.tile_num0 = initial
        bonus.tile_num1 = revealed
        bonus.count = count
        bonus.pos = (ty << 8) | tx
        self._mark_dirty(f"Added {mode.replace('_', ' ')} secret S{slot} to tile ({tx}, {ty}).")
        self.selected_parsed_object = ("secret", slot)
        self._refresh_tile_properties_form(self.selected_tile_context)
        self._refresh_parsed_detail("secret", slot)
        self._redraw_level_overlays()
        self._refresh_info()

    def _draft_object(self, expected_kind: str | None = None):
        if self.placement_draft is None:
            return None
        kind, draft = self.placement_draft
        if expected_kind is not None and kind != expected_kind:
            return None
        return draft

    def _apply_draft_mutation(self, expected_kind: str, label: str, mutator) -> None:
        draft = self._draft_object(expected_kind)
        if draft is None:
            raise ValueError("Choose a placement template first.")
        mutator(draft)
        self.status_text.set(label)
        if not self._suspend_editor_detail_refresh:
            self._refresh_placement_draft_detail()
            self._clear_object_place_ghost()

    def _edit_draft_int_attr(self, kind: str, attr: str, raw: str, *, minimum: int, maximum: int, label: str) -> None:
        value = self._parse_editor_int(raw, minimum=minimum, maximum=maximum, label=label)
        self._apply_draft_mutation(kind, f"Placement default changed: {label}.", lambda obj: setattr(obj, attr, value))

    def _edit_draft_extra_int(self, kind: str, key: str, raw: str, *, minimum: int, maximum: int, label: str) -> None:
        value = self._parse_editor_int(raw, minimum=minimum, maximum=maximum, label=label)
        self._apply_draft_mutation(kind, f"Placement default changed: {label}.", lambda obj: obj.extra.__setitem__(key, value))

    def _set_draft_monster_expert_flag(self, raw: str) -> None:
        enabled = str(raw).strip() not in {"", "0", "false", "False"}
        def mutate(monster):
            monster.type_byte = (monster.type_byte | 0x80) if enabled else (monster.type_byte & 0x7F)
        self._apply_draft_mutation("monster", "Placement monster expert-only flag changed.", mutate)

    def _set_draft_monster_behavior(self, label: str) -> None:
        _labels, mapping = self._monster_behavior_choices()
        if label not in mapping:
            raise ValueError("Unknown monster behavior.")
        movement_type = mapping[label]
        def mutate(monster):
            old_extra = dict(monster.extra)
            monster.type_byte = (monster.type_byte & 0x80) | movement_type
            monster.length = EXPECTED_MONSTER_LENGTHS[movement_type]
            new_extra = monster_extra_defaults(movement_type)
            for key in list(new_extra):
                if key in old_extra and isinstance(new_extra[key], type(old_extra[key])):
                    new_extra[key] = old_extra[key]
            monster.extra = new_extra
            if monster.uses_trigger_rect and monster.trigger_rect_tiles is None:
                monster.x_pos = 0
                monster.y_pos = 0
        self._apply_draft_mutation("monster", "Placement monster behavior changed.", mutate)

    def _set_draft_monster_trigger_size(self, field: str, raw: str) -> None:
        value = self._parse_editor_int(raw, minimum=1, maximum=256, label=f"Trigger {field}") - 1
        draft = self._draft_object("monster")
        if draft is None or not draft.uses_trigger_rect:
            raise ValueError("Choose a trigger-region monster behavior first.")
        tx, ty, tw, th = draft.trigger_rect_tiles or (0, 0, 0, 0)
        if field == "width":
            tw = value
        else:
            th = value
        def mutate(monster):
            monster.x_pos = (ty << 8) | tx
            monster.y_pos = (th << 8) | tw
        self._apply_draft_mutation("monster", f"Placement monster trigger {field} changed.", mutate)

    def _set_draft_platform_behavior(self, label: str) -> None:
        _labels, mapping = self._platform_behavior_choices()
        if label not in mapping:
            raise ValueError("Unknown platform behavior.")
        platform_type = mapping[label]
        def mutate(platform):
            old_type = platform.platform_type
            platform.flags = (platform.flags & 0xF0) | platform_type
            if (old_type == 8) != (platform_type == 8):
                platform.variant, platform.extra = self._platform_default_extra_for_type(platform_type)
            else:
                platform.variant = "type8" if platform_type == 8 else "other"
        self._apply_draft_mutation("platform", "Placement platform behavior changed.", mutate)

    def _template_json_safe(self, value):
        if isinstance(value, dict):
            return {str(key): self._template_json_safe(raw) for key, raw in value.items()}
        if isinstance(value, tuple):
            return [self._template_json_safe(raw) for raw in value]
        if isinstance(value, list):
            return [self._template_json_safe(raw) for raw in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return repr(value)

    def _draft_template_payload(self, kind: str, draft) -> dict[str, object]:
        if self.level is None:
            raise ValueError("Load a level before saving an object template.")
        if kind == "item":
            runtime_sprite = self.sprite_resolver.item_sprite(self.level, draft.sprite_num_raw)
            return {
                "runtime_sprite": runtime_sprite,
                "raw_sprite_fallback": int(draft.sprite_num_raw),
                "y_delta": int(draft.y_delta),
            }
        if kind == "monster":
            runtime_sprite = self.sprite_resolver.monster_sprite(self.level, draft.sprite_num_raw)
            return {
                "runtime_sprite": runtime_sprite,
                "raw_sprite_fallback": int(draft.sprite_num_raw),
                "length": int(draft.length),
                "type_byte": int(draft.type_byte),
                "flags": int(draft.flags),
                "energy": int(draft.energy),
                "respawn_ticks": int(draft.respawn_ticks),
                "current_tick": int(draft.current_tick),
                "score": int(draft.score),
                # The map click supplies placement origin.  Trigger drafts keep
                # only their width/height half of the packed trigger rectangle.
                "trigger_size": list((draft.trigger_rect_tiles or (0, 0, 0, 0))[2:]) if draft.uses_trigger_rect else None,
                "extra": self._template_json_safe(dict(draft.extra)),
            }
        if kind == "platform":
            runtime_sprite = self.sprite_resolver.platform_sprite(self.level, draft.sprite_num_raw)
            return {
                "runtime_sprite": runtime_sprite,
                "raw_sprite_fallback": int(draft.sprite_num_raw),
                "flags": int(draft.flags),
                "variant": str(draft.variant),
                "extra": self._template_json_safe(dict(draft.extra)),
            }
        if kind == "gate":
            return {"scroll_flag": int(draft.scroll_flag)}
        raise ValueError(f"Unsupported template kind {kind!r}.")

    def _draft_from_template_payload(self, kind: str, data: dict[str, object]):
        if self.level is None:
            return None

        def sprite_raw(resolver_name: str) -> int:
            runtime = data.get("runtime_sprite")
            fallback = int(data.get("raw_sprite_fallback", 0))
            if runtime is None:
                return fallback
            resolver = getattr(self.sprite_resolver, resolver_name)
            return int(resolver(self.level, int(runtime)))

        if kind == "item":
            return Item(0, 0, sprite_raw("item_raw_for_runtime"), int(data.get("y_delta", 0)))
        if kind == "monster":
            movement_type = int(data.get("type_byte", 9)) & 0x7F
            length = int(data.get("length", EXPECTED_MONSTER_LENGTHS.get(movement_type, 0)))
            length = EXPECTED_MONSTER_LENGTHS.get(movement_type, length)
            extra_raw = data.get("extra", {})
            extra = dict(extra_raw) if isinstance(extra_raw, dict) else monster_extra_defaults(movement_type)
            monster = Monster(
                raw_offset=-1,
                length=length,
                type_byte=int(data.get("type_byte", movement_type)),
                sprite_num_raw=sprite_raw("monster_raw_for_runtime"),
                flags=int(data.get("flags", 0)),
                energy=int(data.get("energy", 1)),
                respawn_ticks=int(data.get("respawn_ticks", 0)),
                current_tick=int(data.get("current_tick", 0)),
                score=int(data.get("score", 0)),
                x_pos=0,
                y_pos=0,
                extra=extra,
            )
            if monster.uses_trigger_rect:
                trigger_size = data.get("trigger_size")
                tw, th = 0, 0
                if isinstance(trigger_size, list) and len(trigger_size) >= 2:
                    tw, th = int(trigger_size[0]), int(trigger_size[1])
                monster.y_pos = ((th & 0xFF) << 8) | (tw & 0xFF)
            return monster
        if kind == "platform":
            flags = int(data.get("flags", 2))
            platform_type = flags & 0x0F
            variant = str(data.get("variant", "type8" if platform_type == 8 else "other"))
            extra_raw = data.get("extra", {})
            if isinstance(extra_raw, dict):
                extra = dict(extra_raw)
            else:
                _default_variant, extra = self._platform_default_extra_for_type(platform_type)
            return Platform(0, 0, sprite_raw("platform_raw_for_runtime"), flags, variant, extra)
        if kind == "gate":
            return Gate(0, 0, 0, int(data.get("scroll_flag", 0)))
        return None

    def _default_template_name(self, kind: str, draft) -> str:
        return self._template_label(kind, draft).replace("Level — ", "")

    def _new_user_template_id(self, kind: str) -> str:
        used = {str(template.get("id", "")) for template in self.object_templates}
        counter = 1
        while True:
            candidate = f"{kind}-{counter}"
            if candidate not in used:
                return candidate
            counter += 1

    def _save_current_draft_as_template(self) -> None:
        if self.placement_draft is None:
            self.status_text.set("Choose a placement type/template before saving a reusable template.")
            return
        kind, draft = self.placement_draft
        default_name = self._default_template_name(kind, draft)
        name = simpledialog.askstring(
            "Save object template",
            "Template name:",
            initialvalue=default_name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            self.status_text.set("Template save cancelled: empty name.")
            return
        try:
            payload = self._draft_template_payload(kind, draft)
        except ValueError as exc:
            messagebox.showerror("Save template", str(exc))
            return
        template = {
            "id": self._new_user_template_id(kind),
            "kind": kind,
            "name": name,
            "data": payload,
        }
        self.object_templates.append(template)
        self._save_object_templates()
        self.placement_source.set("Templates")
        self._refresh_object_catalog()
        target_iid = f"user:{kind}:{template['id']}"
        if self.object_catalog_tree.exists(target_iid):
            self.object_catalog_tree.selection_set(target_iid)
            self.object_catalog_tree.focus(target_iid)
            self.object_catalog_tree.see(target_iid)
            self._on_object_catalog_selected()
        self.status_text.set(f"Saved {kind} placement template “{name}”.")

    def _save_selected_object_as_template(self) -> None:
        self._commit_all_editor_edit_widgets()
        if self.level is None or self.selected_parsed_object is None:
            self.status_text.set("Select an item, monster, platform, or gate first.")
            return
        kind, index = self.selected_parsed_object
        if kind not in {"item", "monster", "platform", "gate"}:
            self.status_text.set(f"Templates are not supported for selected {kind} objects.")
            return
        draft = self._selected_property_draft_for(kind, index)
        if draft is None:
            draft = self._selected_editor_object(kind, index)
        if draft is None:
            self.status_text.set(f"Selected {kind} {index} no longer exists.")
            return
        default_name = self._default_template_name(kind, draft)
        name = simpledialog.askstring(
            "Save selected object as template",
            "Template name:",
            initialvalue=default_name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            self.status_text.set("Template save cancelled: empty name.")
            return
        try:
            payload = self._draft_template_payload(kind, draft)
        except ValueError as exc:
            messagebox.showerror("Save template", str(exc))
            return
        template = {
            "id": self._new_user_template_id(kind),
            "kind": kind,
            "name": name,
            "data": payload,
        }
        self.object_templates.append(template)
        self._save_object_templates()
        self._refresh_object_catalog()
        self.status_text.set(f"Saved selected {kind} {index} as template “{name}”.")

    def _rename_selected_user_template(self) -> None:
        selection = self.object_catalog_tree.selection() if hasattr(self, "object_catalog_tree") else ()
        if not selection:
            self.status_text.set("Select a saved template to rename.")
            return
        parts = str(selection[0]).split(":", 2)
        if len(parts) != 3 or parts[0] != "user":
            self.status_text.set("Only saved user templates can be renamed here.")
            return
        _source, kind, template_id = parts
        template = next((row for row in self.object_templates if str(row.get("id", "")) == template_id), None)
        if template is None:
            self.status_text.set("Saved template was not found.")
            return
        current_name = str(template.get("name", "Saved template"))
        name = simpledialog.askstring(
            "Rename object template",
            "Template name:",
            initialvalue=current_name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            self.status_text.set("Template rename cancelled: empty name.")
            return
        template["name"] = name
        self._save_object_templates()
        self._refresh_object_catalog()
        target_iid = f"user:{kind}:{template_id}"
        if self.object_catalog_tree.exists(target_iid):
            self.object_catalog_tree.selection_set(target_iid)
            self.object_catalog_tree.focus(target_iid)
            self.object_catalog_tree.see(target_iid)
        self.status_text.set(f"Renamed saved {kind} template to “{name}”.")

    def _delete_selected_user_template(self) -> None:
        selection = self.object_catalog_tree.selection() if hasattr(self, "object_catalog_tree") else ()
        if not selection:
            self.status_text.set("Select a saved template to delete.")
            return
        parts = str(selection[0]).split(":", 2)
        if len(parts) != 3 or parts[0] != "user":
            self.status_text.set("Only saved user templates can be deleted here.")
            return
        _source, kind, template_id = parts
        before = len(self.object_templates)
        self.object_templates = [template for template in self.object_templates if str(template.get("id", "")) != template_id]
        if len(self.object_templates) == before:
            self.status_text.set("Saved template was not found.")
            return
        self._save_object_templates()
        self._refresh_object_catalog()
        self.status_text.set(f"Deleted saved {kind} template.")

    def _build_placement_draft(self) -> None:
        self.placement_draft = None
        if self.level is None or self.placement_catalog_selection is None:
            return
        kind, source, payload = self.placement_catalog_selection

        if source == "user":
            template = next((row for row in self.object_templates if row.get("kind") == kind and row.get("id") == payload), None)
            data = template.get("data") if isinstance(template, dict) else None
            if not isinstance(data, dict):
                return
            draft = self._draft_from_template_payload(kind, data)
            if draft is not None:
                self.placement_draft = (kind, draft)
            return

        if source == "level":
            template_index = int(payload) if payload is not None else -1
            if kind == "item" and 0 <= template_index < len(self.level.items):
                self.placement_draft = (kind, copy.deepcopy(self.level.items[template_index]))
                return
            if kind == "monster" and 0 <= template_index < len(self.level.monsters):
                template = copy.deepcopy(self.level.monsters[template_index])
                template.raw_offset = -1
                self.placement_draft = (kind, template)
                return
            if kind == "platform" and 0 <= template_index < len(self.level.platforms):
                self.placement_draft = (kind, copy.deepcopy(self.level.platforms[template_index]))
                return
            if kind == "gate" and 0 <= template_index < len(self.level.gates):
                template = self.level.gates[template_index]
                self.placement_draft = (kind, Gate(0, 0, 0, template.scroll_flag))
                return
            return

        # New object presets are level-independent type starters.  The draft
        # inspector fills in behavior and authored details after this first pick.
        if kind == "item":
            runtime_sprite = int(payload) if payload is not None else sorted(ITEM_VISUAL_NAMES)[0]
            raw_sprite = self.sprite_resolver.item_raw_for_runtime(self.level, runtime_sprite)
            self.placement_draft = (kind, Item(0, 0, raw_sprite, 0))
        elif kind == "monster":
            runtime_sprite = int(payload) if payload is not None else MONSTER_PLACEMENT_VISUALS[0]
            movement_type = MONSTER_PLACEMENT_DEFAULT_BEHAVIOR.get(runtime_sprite, 9)
            raw_sprite = self.sprite_resolver.monster_raw_for_runtime(self.level, runtime_sprite)
            monster = Monster(
                raw_offset=-1,
                length=EXPECTED_MONSTER_LENGTHS[movement_type],
                type_byte=movement_type,
                sprite_num_raw=raw_sprite,
                flags=0,
                energy=1,
                respawn_ticks=0,
                current_tick=0,
                score=0,
                x_pos=0,
                y_pos=0,
                extra=monster_extra_defaults(movement_type),
            )
            self.placement_draft = (kind, monster)
        elif kind == "platform":
            runtime_sprite = int(payload) if payload is not None else sorted(PLATFORM_VISUAL_NAMES)[0]
            raw_sprite = self.sprite_resolver.platform_raw_for_runtime(self.level, runtime_sprite)
            variant, extra = self._platform_default_extra_for_type(2)
            self.placement_draft = (kind, Platform(0, 0, raw_sprite, 2, variant, extra))
        elif kind == "gate":
            self.placement_draft = (kind, Gate(0, 0, 0, 0))

    def _refresh_placement_draft_detail(self) -> None:
        self._clear_parsed_detail_controls()
        parent = self.tables_detail_form.inner
        if hasattr(self, "tables_detail_controls"):
            self.tables_detail_controls.configure(text="Placement defaults")
        draft_info = self.placement_draft
        if self.level is None or draft_info is None:
            self._render_parsed_detail_sprite(None, caption="No placement template")
            self._editor_note(parent, "Choose an item, monster, platform, or gate template on the left. Its editable defaults will appear here before placement.")
            return
        kind, draft = draft_info
        self._editor_note(parent, "These values are copied into each newly placed object. The map click still supplies the placement position.")
        if kind == "item":
            sprite_view = self.sprite_resolver.item_sprite(self.level, draft.sprite_num_raw)
            self._render_parsed_detail_sprite(sprite_view, caption=item_visual_name(sprite_view))
            general = self._editor_section(parent, "Item placement")
            item_visual_labels, _item_visual_mapping = self._item_visual_choices()
            selected_item_visual = self._item_visual_label_for_runtime(sprite_view)
            self._add_editor_combo(general, "Visual", selected_item_visual, 0, values=item_visual_labels, editable=True, on_select=self._set_draft_item_visual)
            self._add_editor_entry(general, "Sprite raw number", draft.sprite_num_raw, 1)
            self._add_editor_spinbox(general, "Y delta / tuning", draft.y_delta, 2, from_=-128, to=127, editable=True,
                                     on_commit=lambda raw: self._edit_draft_int_attr("item", "y_delta", raw, minimum=-128, maximum=127, label="Y delta / tuning"))
            return
        if kind == "monster":
            sprite_view = self.sprite_resolver.monster_sprite(self.level, draft.sprite_num_raw)
            visual_name = monster_visual_name(sprite_view, draft.movement_type)
            visual_labels, _visual_mapping = self._monster_visual_choices(draft.movement_type)
            selected_visual = self._monster_visual_label_for_runtime(sprite_view, draft.movement_type)
            labels, _mapping = self._monster_behavior_choices(draft)
            self._render_parsed_detail_sprite(sprite_view, caption=visual_name)
            general = self._editor_section(parent, "Monster placement")
            self._add_editor_combo(general, "Visual", selected_visual, 0, values=visual_labels, editable=True, on_select=self._set_draft_monster_visual)
            self._add_editor_combo(general, "Behavior", draft.behavior_name, 1, values=labels, editable=True, on_select=self._set_draft_monster_behavior)
            self._add_editor_spinbox(general, "Energy", draft.energy, 2, from_=0, to=255, editable=True,
                                     on_commit=lambda raw: self._edit_draft_int_attr("monster", "energy", raw, minimum=0, maximum=255, label="Energy"))
            self._add_editor_spinbox(general, "Score value", draft.score, 3, from_=0, to=255, editable=True,
                                     on_commit=lambda raw: self._edit_draft_int_attr("monster", "score", raw, minimum=0, maximum=255, label="Score value"))
            self._add_editor_spinbox(general, "Respawn / activation ticks", draft.respawn_ticks, 4, from_=0, to=255, editable=True,
                                     on_commit=lambda raw: self._edit_draft_int_attr("monster", "respawn_ticks", raw, minimum=0, maximum=255, label="Respawn / activation ticks"))
            flags = self._editor_section(parent, "Flags")
            self._add_readonly_checkbox(flags, "Expert-only enemy", draft.expert_only, future_editable=True,
                                        note="Copied into the new monster type byte.",
                                        on_toggle=self._set_draft_monster_expert_flag)
            self._add_readonly_checkbox(flags, "Uses trigger rectangle", draft.uses_trigger_rect, future_editable=False,
                                        note="Derived from the selected behavior.")
            placement = self._editor_section(parent, "Placement / spawn region")
            if draft.uses_trigger_rect:
                trigger = draft.trigger_rect_tiles or (0, 0, 0, 0)
                tx, ty, tw, th = trigger
                self._add_editor_spinbox(placement, "Trigger width", tw + 1, 0, from_=1, to=256, editable=True,
                                         on_commit=lambda raw: self._set_draft_monster_trigger_size("width", raw))
                self._add_editor_spinbox(placement, "Trigger height", th + 1, 1, from_=1, to=256, editable=True,
                                         on_commit=lambda raw: self._set_draft_monster_trigger_size("height", raw))
                self._editor_note(placement, "Map click sets the trigger rectangle origin; width and height come from this draft.")
            else:
                self._editor_note(placement, "Map click sets the fixed monster anchor / spawn point.")
            params = self._editor_section(parent, "Behavior parameters")
            behavior_row = 0
            for key, value in draft.extra.items():
                if key.startswith("initial_runtime_") or key == "record_tail_bytes":
                    continue
                if isinstance(value, (int, float)):
                    label = self._monster_extra_label(key)
                    minimum, maximum = (-32768, 32767) if key.endswith("_px") or key.endswith("_step") else (0, 255)
                    if key in {"patrol_left_x_px", "patrol_right_x_px"}:
                        minimum, maximum = -32768, 32767
                    self._add_editor_spinbox(params, label, value, behavior_row, from_=minimum, to=maximum, editable=True,
                                             on_commit=lambda raw, k=key, mn=minimum, mx=maximum, lbl=label: self._edit_draft_extra_int("monster", k, raw, minimum=mn, maximum=mx, label=lbl))
                else:
                    self._add_editor_entry(params, self._monster_extra_label(key), value, behavior_row)
                behavior_row += 1
            if behavior_row == 0:
                self._editor_note(params, "No additional authored parameters parsed for this behavior.")
            return
        if kind == "platform":
            sprite_view = self.sprite_resolver.platform_sprite(self.level, draft.sprite_num_raw)
            visual_name = platform_visual_name(sprite_view)
            behavior_name = platform_behavior_name(draft.platform_type)
            labels, _mapping = self._platform_behavior_choices()
            selected_label = next((label for label in labels if _mapping[label] == draft.platform_type), behavior_name)
            self._render_parsed_detail_sprite(sprite_view, caption=visual_name)
            general = self._editor_section(parent, "Platform placement")
            platform_visual_labels, _platform_visual_mapping = self._platform_visual_choices()
            selected_platform_visual = self._platform_visual_label_for_runtime(sprite_view)
            self._add_editor_combo(general, "Visual", selected_platform_visual, 0, values=platform_visual_labels, editable=True, on_select=self._set_draft_platform_visual)
            self._add_editor_entry(general, "Sprite raw number", draft.sprite_num_raw, 1)
            self._add_editor_combo(general, "Behavior", selected_label, 2, values=labels, editable=True, on_select=self._set_draft_platform_behavior)
            params = self._editor_section(parent, "Behavior parameters")
            for row, (key, value) in enumerate(draft.extra.items()):
                if isinstance(value, int):
                    self._add_editor_spinbox(params, self._platform_extra_label(draft, key), value, row, from_=-32768, to=65535, editable=True,
                                             on_commit=lambda raw, k=key, lbl=self._platform_extra_label(draft, key): self._edit_draft_extra_int("platform", k, raw, minimum=-32768, maximum=65535, label=lbl))
                else:
                    self._add_editor_entry(params, self._platform_extra_label(draft, key), value, row)
            return
        if kind == "gate":
            self._render_parsed_detail_sprite(None, caption="Gate")
            general = self._editor_section(parent, "Gate placement")
            self._add_editor_spinbox(general, "Scroll flag", draft.scroll_flag, 0, from_=0, to=255, editable=True,
                                     on_commit=lambda raw: self._edit_draft_int_attr("gate", "scroll_flag", raw, minimum=0, maximum=255, label="Scroll flag"))
            self._editor_note(parent, "First map click places the source; the next map click picks the destination.")
            return

    def _build_parsed_detail_controls(self, kind: str, index: int) -> None:
        self._clear_parsed_detail_controls()
        if hasattr(self, "tables_detail_controls"):
            self.tables_detail_controls.configure(text="Selected object properties")
        if self.level is None:
            return
        parent = self.tables_detail_form.inner
        self._ensure_selected_property_draft(kind, index)
        self._add_selected_property_apply_bar(parent, kind, index)

        if kind == "monster" and 0 <= index < len(self.level.monsters):
            monster = self._selected_property_draft_for("monster", index)
            sprite_view = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
            visual_name = monster_visual_name(sprite_view, monster.movement_type)
            title = monster_display_name(sprite_view, monster.behavior_name, monster.movement_type)
            self._editor_note(parent, f"{title}. Select and drag on the map to reposition it. Save writes the edited LEVEL*.SQZ.")

            general = self._editor_section(parent, "Enemy")
            behavior_labels, _behavior_mapping = self._monster_behavior_choices(monster)
            visual_labels, _visual_mapping = self._monster_visual_choices(monster.movement_type)
            selected_visual = self._monster_visual_label_for_runtime(sprite_view, monster.movement_type)
            self._add_editor_combo(general, "Visual", selected_visual, 0, values=visual_labels, editable=True, on_select=lambda label, i=index: self._set_monster_visual(i, label))
            self._add_editor_combo(general, "Behavior", monster.behavior_name, 1, values=behavior_labels, editable=True, on_select=lambda label, i=index: self._set_monster_behavior(i, label))
            self._editor_note(parent, "Behavior changes are staged. Apply accepts any known behavior and save rebuilds the compact variable-length monster table.")
            self._add_editor_spinbox(general, "Energy", monster.energy, 2, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("monster", i, "energy", raw, minimum=0, maximum=255, label="Energy"))
            self._add_editor_spinbox(general, "Score value", monster.score, 3, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("monster", i, "score", raw, minimum=0, maximum=255, label="Score value"))
            self._add_editor_spinbox(general, "Respawn / activation ticks", monster.respawn_ticks, 4, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("monster", i, "respawn_ticks", raw, minimum=0, maximum=255, label="Respawn / activation ticks"))

            flags = self._editor_section(parent, "Flags")
            self._add_readonly_checkbox(flags, "Expert-only enemy", monster.expert_only, future_editable=True, note="Stored in the level type byte.", on_toggle=lambda raw, i=index: self._set_monster_expert_flag(i, raw))
            if monster.movement_type == 9:
                self._add_readonly_checkbox(flags, "Patrol follows terrain / ground physics", bool(monster.flags & 0x08), future_editable=True, note="Confirmed authored T9 flag.", on_toggle=lambda raw, i=index: self._set_monster_patrol_ground_flag(i, raw))
            self._add_readonly_checkbox(flags, "Uses trigger rectangle instead of fixed spawn point", monster.uses_trigger_rect, future_editable=False, note="Derived from the selected behavior.")
            self._add_readonly_checkbox(flags, "Uses fixed anchor / placement point", not monster.uses_trigger_rect, future_editable=False, note="Derived from the selected behavior.")

            placement = self._editor_section(parent, "Placement")
            if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                tx, ty, tw, th = monster.trigger_rect_tiles
                self._add_editor_spinbox(placement, "Trigger X tile", tx, 0, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_monster_trigger_field(i, "x", raw))
                self._add_editor_spinbox(placement, "Trigger Y tile", ty, 1, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_monster_trigger_field(i, "y", raw))
                self._add_editor_spinbox(placement, "Trigger width", tw + 1, 2, from_=1, to=256, editable=True, on_commit=lambda raw, i=index: self._set_monster_trigger_field(i, "width", raw))
                self._add_editor_spinbox(placement, "Trigger height", th + 1, 3, from_=1, to=256, editable=True, on_commit=lambda raw, i=index: self._set_monster_trigger_field(i, "height", raw))
            else:
                self._add_editor_spinbox(placement, "Anchor X", monster.x_pos, 0, from_=0, to=65535, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("monster", i, "x_pos", raw, minimum=0, maximum=65535, label="Anchor X"))
                self._add_editor_spinbox(placement, "Anchor Y", monster.y_pos, 1, from_=0, to=65535, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("monster", i, "y_pos", raw, minimum=0, maximum=65535, label="Anchor Y"))

            behavior = self._editor_section(parent, "Behavior parameters")
            behavior_row = 0
            for key, value in monster.extra.items():
                if key.startswith("initial_runtime_") or key == "record_tail_bytes":
                    continue
                if isinstance(value, (int, float)):
                    label = self._monster_extra_label(key)
                    minimum, maximum = (-32768, 32767) if key.endswith("_px") or key.endswith("_step") else (0, 255)
                    if key in {"patrol_left_x_px", "patrol_right_x_px"}:
                        minimum, maximum = -32768, 32767
                    self._add_editor_spinbox(behavior, label, value, behavior_row, from_=minimum, to=maximum, editable=True, on_commit=lambda raw, i=index, k=key, mn=minimum, mx=maximum, lbl=label: self._edit_selected_extra_int("monster", i, k, raw, minimum=mn, maximum=mx, label=lbl))
                else:
                    self._add_editor_entry(behavior, self._monster_extra_label(key), value, behavior_row)
                behavior_row += 1
            if behavior_row == 0:
                self._editor_note(behavior, "No additional authored parameters parsed for this behavior.")

            runtime = self._editor_section(parent, "Runtime / engine-derived")
            trigger_cycle = monster.movement_type in (0, 10)
            has_reset_runtime_fields = any(key.startswith("initial_runtime_") for key in monster.extra)
            self._add_readonly_checkbox(runtime, "Spawn cycle is controlled by trigger/spawner behavior", trigger_cycle, future_editable=False)
            self._add_readonly_checkbox(runtime, "Record contains runtime-init fields reset by the engine", has_reset_runtime_fields, future_editable=False)

            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Level record", f"M{index}", 0)
            self._add_editor_entry(raw, "Behavior type", f"T{monster.movement_type}", 1)
            self._add_editor_entry(raw, "Raw sprite number", monster.sprite_num_raw, 2)
            self._add_editor_entry(raw, "Resolved sprite frame", sprite_view, 3)
            self._add_editor_entry(raw, "Raw flags", f"0x{monster.flags:02X}", 4)
            self._add_editor_entry(raw, "Record length", monster.length, 5)
            return

        if kind == "item" and 0 <= index < len(self.level.items):
            item = self._selected_property_draft_for("item", index)
            sprite_view = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
            item_name = item_visual_name(sprite_view)
            self._editor_note(parent, "Select and drag this item on the map, delete it, or place new item slots from the Place tab.")
            general = self._editor_section(parent, "Item")
            item_visual_labels, _item_visual_mapping = self._item_visual_choices()
            selected_item_visual = self._item_visual_label_for_runtime(sprite_view)
            self._add_editor_combo(general, "Item type", selected_item_visual, 0, values=item_visual_labels, editable=True, on_select=lambda label, i=index: self._set_item_visual(i, label))
            self._add_readonly_checkbox(general, "Item slot is active", item.active, future_editable=True, note="Unchecking keeps the fixed slot but disables the in-game item; checking revives an empty slot with a default visual that you can change above.", on_toggle=lambda raw, i=index: self._set_item_active(i, raw))
            placement = self._editor_section(parent, "Placement")
            self._add_editor_spinbox(placement, "X", item.x_pos, 0, from_=-32768, to=32767, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("item", i, "x_pos", raw, minimum=-32768, maximum=32767, label="X"))
            self._add_editor_spinbox(placement, "Y", item.y_pos, 1, from_=-32768, to=32767, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("item", i, "y_pos", raw, minimum=-32768, maximum=32767, label="Y"))
            self._add_editor_spinbox(placement, "Y delta / tuning", item.y_delta, 2, from_=-128, to=127, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("item", i, "y_delta", raw, minimum=-128, maximum=127, label="Y delta / tuning"))
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Level record", f"I{index}", 0)
            self._add_editor_entry(raw, "Raw sprite number", item.sprite_num_raw, 1)
            self._add_editor_entry(raw, "Resolved sprite frame", sprite_view, 2)
            return

        if kind == "platform" and 0 <= index < len(self.level.platforms):
            platform = self._selected_property_draft_for("platform", index)
            sprite_view = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
            visual_name = platform_visual_name(sprite_view)
            behavior_name = platform_behavior_name(platform.platform_type)
            self._editor_note(parent, "Select and drag this platform on the map, delete it, or place another platform from the Place tab.")
            general = self._editor_section(parent, "Platform")
            platform_behavior_labels, platform_behavior_mapping = self._platform_behavior_choices()
            selected_platform_behavior_label = next((label for label in platform_behavior_labels if platform_behavior_mapping[label] == platform.platform_type), behavior_name)
            platform_visual_labels, _platform_visual_mapping = self._platform_visual_choices()
            selected_platform_visual = self._platform_visual_label_for_runtime(sprite_view)
            self._add_editor_combo(general, "Visual", selected_platform_visual, 0, values=platform_visual_labels, editable=True, on_select=lambda label, i=index: self._set_platform_visual(i, label))
            self._add_editor_combo(general, "Behavior", selected_platform_behavior_label, 1, values=platform_behavior_labels, editable=True, on_select=lambda label, i=index: self._set_platform_behavior(i, label))
            self._add_readonly_checkbox(general, "Platform slot is active", platform.active, future_editable=True, note="Unchecking disables this fixed platform slot; checking revives an empty slot with a default visual that you can change above.", on_toggle=lambda raw, i=index: self._set_platform_active(i, raw))
            placement = self._editor_section(parent, "Placement")
            self._add_editor_spinbox(placement, "X", platform.x_pos, 0, from_=0, to=65535, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("platform", i, "x_pos", raw, minimum=0, maximum=65535, label="X"))
            self._add_editor_spinbox(placement, "Y", platform.y_pos, 1, from_=0, to=65535, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("platform", i, "y_pos", raw, minimum=0, maximum=65535, label="Y"))
            params = self._editor_section(parent, "Behavior parameters")
            row = 0
            for key, value in platform.extra.items():
                label = self._platform_extra_label(platform, key)
                if isinstance(value, (int, float)):
                    signed_keys = {"max_velocity", "velocity"}
                    minimum, maximum = (-128, 127) if key in signed_keys else (0, 65535 if key in {"unkA", "counter", "y_delta"} else 255)
                    self._add_editor_spinbox(params, label, value, row, from_=minimum, to=maximum, editable=True, on_commit=lambda raw, i=index, k=key, mn=minimum, mx=maximum, lbl=label: self._edit_selected_extra_int("platform", i, k, raw, minimum=mn, maximum=mx, label=lbl))
                else:
                    self._add_editor_entry(params, label, value, row)
                row += 1
            if row == 0:
                self._editor_note(params, "No additional platform parameters parsed for this record.")
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Platform slot", index, 0)
            self._add_editor_entry(raw, "Raw sprite number", platform.sprite_num_raw, 1)
            self._add_editor_entry(raw, "Resolved sprite frame", sprite_view, 2)
            self._add_editor_entry(raw, "Raw flags", f"0x{platform.flags:02X}", 3)
            self._add_editor_entry(raw, "Raw behavior type", platform.platform_type, 4)
            for raw_row, (key, value) in enumerate(platform.extra.items(), start=5):
                self._add_editor_entry(raw, f"raw {key}", value, raw_row)
            return

        if kind == "gate" and 0 <= index < len(self.level.gates):
            gate = self._selected_property_draft_for("gate", index)
            enter_xy = self.level.tilemap_xy(gate.enter_pos)
            dst_xy = self.level.tilemap_xy(gate.dst_pos)
            focus_side = self.gate_focus_side.get(index, "source")
            self._editor_note(parent, "Click the same gate row, click the focused endpoint in the map again, or use the button to jump between source and destination.")
            general = self._editor_section(parent, "Gate")
            self._add_editor_combo(general, "Focused endpoint", focus_side.title(), 0)
            self._add_editor_entry(general, "Gate", f"Gate {index}", 1)
            ttk.Button(general, text="Switch focused endpoint", command=lambda i=index: self._toggle_gate_focus_endpoint(i)).grid(
                row=2,
                column=0,
                columnspan=2,
                sticky="ew",
                pady=(4, 0),
            )
            ttk.Button(general, text="Pick source on map", command=lambda i=index: self._begin_gate_pick(i, "source")).grid(
                row=3,
                column=0,
                columnspan=2,
                sticky="ew",
                pady=(4, 0),
            )
            ttk.Button(general, text="Pick destination on map", command=lambda i=index: self._begin_gate_pick(i, "destination")).grid(
                row=4,
                column=0,
                columnspan=2,
                sticky="ew",
                pady=(4, 0),
            )
            source = self._editor_section(parent, "Source / entry")
            self._add_editor_spinbox(source, "Tile X", enter_xy[0], 0, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_gate_tile_coord(i, "source", "x", raw))
            self._add_editor_spinbox(source, "Tile Y", enter_xy[1], 1, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_gate_tile_coord(i, "source", "y", raw))
            dest = self._editor_section(parent, "Destination")
            self._add_editor_spinbox(dest, "Tile X", dst_xy[0], 0, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_gate_tile_coord(i, "destination", "x", raw))
            self._add_editor_spinbox(dest, "Tile Y", dst_xy[1], 1, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_gate_tile_coord(i, "destination", "y", raw))
            options = self._editor_section(parent, "Options")
            self._add_editor_spinbox(options, "Scroll flag", gate.scroll_flag, 0, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("gate", i, "scroll_flag", raw, minimum=0, maximum=255, label="Scroll flag"))
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "enter_pos", f"0x{gate.enter_pos:04X}", 0)
            self._add_editor_entry(raw, "tilemap_pos", f"0x{gate.tilemap_pos:04X}", 1)
            self._add_editor_entry(raw, "dst_pos", f"0x{gate.dst_pos:04X}", 2)
            return

        if kind == "column" and 0 <= index < len(self.level.columns):
            column = self._selected_property_draft_for("column", index)
            map_xy = self.level.tilemap_xy(column.tilemap_pos)
            trigger_xy = self.level.tilemap_xy(column.trigger_pos)
            general = self._editor_section(parent, "Shifting column")
            self._add_editor_spinbox(general, "Origin tile X", map_xy[0], 0, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_column_tile_coord(i, "origin", "x", raw))
            self._add_editor_spinbox(general, "Origin tile Y", map_xy[1], 1, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_column_tile_coord(i, "origin", "y", raw))
            self._add_editor_spinbox(general, "Width", column.width, 2, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("column", i, "width", raw, minimum=0, maximum=255, label="Width"))
            self._add_editor_spinbox(general, "Height", column.height, 3, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("column", i, "height", raw, minimum=0, maximum=255, label="Height"))
            trigger = self._editor_section(parent, "Trigger / target")
            self._add_editor_spinbox(trigger, "Trigger tile X", trigger_xy[0], 0, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_column_tile_coord(i, "trigger", "x", raw))
            self._add_editor_spinbox(trigger, "Trigger tile Y", trigger_xy[1], 1, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_column_tile_coord(i, "trigger", "y", raw))
            self._add_editor_spinbox(trigger, "Target Y", column.y_target, 2, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._edit_selected_int_attr("column", i, "y_target", raw, minimum=0, maximum=255, label="Target Y"))
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Tiles buffer offset", f"0x{column.tiles_offset_buf:04X}", 0)
            self._add_editor_entry(raw, "Runtime state / tuning byte", column.unk9, 1)
            self._add_editor_entry(raw, "raw unk9", column.unk9, 2)
            return

        if kind == "secret" and 0 <= index < len(self.level.bonuses):
            bonus = self._selected_property_draft_for("secret", index)
            pos_xy = self.level.tilemap_xy(bonus.pos)
            secret_names = {
                "small_random_bonus": "Small random bonus secret",
                "tile_reveal": "Tile reveal / appearing structure secret",
                "big_random_bonus": "Big random bonus secret",
            }
            general = self._editor_section(parent, "Secret")
            secret_type_labels = [
                "Small random bonus secret",
                "Tile reveal / appearing structure secret",
                "Big random bonus secret",
            ]
            self._add_editor_combo(
                general,
                "Secret type",
                secret_names.get(bonus.mode, bonus.mode),
                0,
                values=secret_type_labels,
                editable=True,
                on_select=lambda label, i=index: self._set_secret_mode(i, label),
            )
            self._add_editor_spinbox(general, "Estimated hit count", bonus.hit_count_estimate, 1, from_=1, to=128, editable=True, on_commit=lambda raw, i=index: self._set_secret_hit_count(i, raw))
            location = self._editor_section(parent, "Location / secret tiles")
            self._add_editor_spinbox(location, "Tile X", pos_xy[0], 0, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_secret_tile_coord(i, "x", raw))
            self._add_editor_spinbox(location, "Tile Y", pos_xy[1], 1, from_=0, to=255, editable=True, on_commit=lambda raw, i=index: self._set_secret_tile_coord(i, "y", raw))
            self._add_editor_entry(location, "Initial tile", f"0x{bonus.tile_num0:02X}", 2, editable=True, on_commit=lambda raw, i=index: self._set_secret_tile_value(i, "tile_num0", raw))
            self._add_editor_entry(location, "Revealed tile", f"0x{bonus.tile_num1:02X}", 3, editable=True, on_commit=lambda raw, i=index: self._set_secret_tile_value(i, "tile_num1", raw))
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Secret slot", f"S{index}", 0)
            self._add_editor_entry(raw, "Count byte", f"0x{bonus.count:02X}", 1)
            self._add_editor_entry(raw, "Tilemap pos", f"0x{bonus.pos:04X}", 2)
            return

        if kind == "boss":
            boss = self._selected_property_draft_for("boss", 0)
            general = self._editor_section(parent, "Boss controller")
            self._add_readonly_checkbox(general, "Boss controller active", boss.active, future_editable=True, note="Active state is stored through the boss state byte; disabling sets it to 0xFF.", on_toggle=self._set_boss_active)
            self._add_editor_spinbox(general, "Anchor X", boss.x_pos, 0, from_=0, to=65535, editable=True, on_commit=lambda raw: self._edit_selected_int_attr("boss", 0, "x_pos", raw, minimum=0, maximum=65535, label="Anchor X"))
            self._add_editor_spinbox(general, "Anchor Y", boss.y_pos, 1, from_=0, to=65535, editable=True, on_commit=lambda raw: self._edit_selected_int_attr("boss", 0, "y_pos", raw, minimum=0, maximum=65535, label="Anchor Y"))
            self._add_editor_spinbox(general, "Allowed X min", boss.x_min, 2, from_=0, to=65535, editable=True, on_commit=lambda raw: self._edit_selected_int_attr("boss", 0, "x_min", raw, minimum=0, maximum=65535, label="Allowed X min"))
            self._add_editor_spinbox(general, "Allowed X max", boss.x_max, 3, from_=0, to=65535, editable=True, on_commit=lambda raw: self._edit_selected_int_attr("boss", 0, "x_max", raw, minimum=0, maximum=65535, label="Allowed X max"))
            self._add_editor_spinbox(general, "Speed", boss.speed, 4, from_=0, to=255, editable=True, on_commit=lambda raw: self._edit_selected_int_attr("boss", 0, "speed", raw, minimum=0, maximum=255, label="Speed"))
            self._add_editor_spinbox(general, "Energy", boss.energy, 5, from_=-32768, to=32767, editable=True, on_commit=lambda raw: self._edit_selected_int_attr("boss", 0, "energy", raw, minimum=-32768, maximum=32767, label="Energy"))
            self._add_editor_spinbox(general, "State", boss.state, 6, from_=0, to=255, editable=True, on_commit=lambda raw: self._edit_selected_int_attr("boss", 0, "state", raw, minimum=0, maximum=255, label="State"))
            return

        self._editor_note(parent, "Select an object on the left to inspect and edit it on the map.")

    def _set_parsed_detail_text(self, text: str) -> None:
        self._parsed_detail_text_cache = text
    def _render_parsed_detail_sprite(self, sprite_num: int | None, *, caption: str = "") -> None:
        canvas = self.tables_detail_canvas
        canvas.delete("all")
        self.tables_detail_tile_photos.clear()
        canvas.update_idletasks()
        width = max(220, canvas.winfo_width() or 220)
        height = max(180, canvas.winfo_height() or 180)
        canvas.create_rectangle(0, 0, width, height, fill="#202020", outline="")
        if sprite_num is None or not self.sprites_blob or not 0 <= sprite_num < self.sprite_tables.count:
            canvas.create_text(width / 2, height / 2, text="No sprite preview", fill="#B0B0B0")
            if caption:
                canvas.create_text(width / 2, height - 14, text=caption, fill="#D0D0D0")
            self.tables_detail_photo = None
            return
        try:
            sprite = render_sprite_image(
                self.sprites_blob,
                self.sprite_tables,
                self.palettes[self.current_level_index],
                sprite_num,
                scale=3,
            )
        except Exception as exc:
            canvas.create_text(width / 2, height / 2, text=f"Preview failed:\n{exc}", fill="#FF8080")
            self.tables_detail_photo = None
            return
        self.tables_detail_photo = ImageTk.PhotoImage(sprite)
        canvas.create_image(width / 2, height / 2 - (10 if caption else 0), image=self.tables_detail_photo, anchor="center")
        if caption:
            canvas.create_text(width / 2, height - 14, text=caption, fill="#D0D0D0")

    def _render_secret_detail_tiles(self, bonus, *, caption: str) -> None:
        canvas = self.tables_detail_canvas
        canvas.delete("all")
        self.tables_detail_photo = None
        self.tables_detail_tile_photos.clear()
        canvas.update_idletasks()
        width = max(220, canvas.winfo_width() or 220)
        height = max(180, canvas.winfo_height() or 180)
        canvas.create_rectangle(0, 0, width, height, fill="#202020", outline="")
        if self.level is None:
            canvas.create_text(width / 2, height / 2, text="No tile preview", fill="#B0B0B0")
            return
        try:
            initial = render_tile_image(self.level, self.union_tiles, self.palettes[self.current_level_index], bonus.initial_tile, scale=4).convert("RGBA")
            revealed = render_tile_image(self.level, self.union_tiles, self.palettes[self.current_level_index], bonus.revealed_tile, scale=4).convert("RGBA")
        except Exception as exc:
            canvas.create_text(width / 2, height / 2, text=f"Tile preview failed:\n{exc}", fill="#FF8080")
            return

        fade_both = bonus.mode == "tile_reveal" or bonus.initial_tile != bonus.revealed_tile
        if fade_both:
            initial.putalpha(165)
            revealed.putalpha(165)

        checker = Image.new("RGBA", (64, 64), (40, 40, 40, 255))
        draw = ImageDraw.Draw(checker)
        for y in range(0, 64, 8):
            for x in range(0, 64, 8):
                fill = (72, 72, 72, 255) if (x // 8 + y // 8) % 2 else (45, 45, 45, 255)
                draw.rectangle((x, y, x + 7, y + 7), fill=fill)

        left_x = max(12, int(width / 2 - 78))
        right_x = min(int(width - 76), int(width / 2 + 14))
        y = max(20, int(height / 2 - 46))
        labels = [("Initial", initial, left_x), ("Revealed", revealed, right_x)]
        for label, tile, x in labels:
            composed = checker.copy()
            composed.alpha_composite(tile, (0, 0))
            photo = ImageTk.PhotoImage(composed)
            self.tables_detail_tile_photos.append(photo)
            canvas.create_image(x, y, image=photo, anchor="nw")
            canvas.create_rectangle(x, y, x + 64, y + 64, outline="#808080")
            canvas.create_text(x + 32, y - 8, text=label, fill="#D0D0D0")
        canvas.create_text(width / 2, height - 14, text=caption, fill="#D0D0D0")

    def _center_level_on_world_point(self, world_x: float, world_y: float) -> None:
        if self.level is None:
            return
        canvas = self.level_canvas.canvas
        canvas.update_idletasks()
        scale = self.zoom.get()
        point_x = world_x * scale
        point_y = world_y * scale
        view_w = max(1, canvas.winfo_width())
        view_h = max(1, canvas.winfo_height())
        map_w = max(1, self.level.width_tiles * 16 * scale)
        map_h = max(1, self.level.height_tiles * 16 * scale)
        target_x = max(0, min(map_w - view_w, point_x - view_w / 2))
        target_y = max(0, min(map_h - view_h, point_y - view_h / 2))
        canvas.xview_moveto(target_x / map_w)
        canvas.yview_moveto(target_y / map_h)
        self.level_canvas.position_viewport_background()
        self._redraw_animated_tile_overlay()

    def _object_focus_point(self, kind: str, index: int) -> tuple[float, float] | None:
        if self.level is None:
            return None
        if kind == "monster":
            if not (0 <= index < len(self.level.monsters)):
                return None
            monster = self.level.monsters[index]
            if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                tx, ty, tw, th = monster.trigger_rect_tiles
                return ((tx + (tw + 1) / 2) * 16, (ty + (th + 1) / 2) * 16)
            return float(monster.x_pos), float(monster.y_pos)
        if kind == "item":
            item = self.level.items[index]
            if not item.active:
                return None
            return float(item.x_pos), float(item.y_pos)
        if kind == "platform":
            platform = self.level.platforms[index]
            if not platform.active:
                return None
            return float(platform.x_pos), float(platform.y_pos)
        if kind == "gate":
            gate = self.level.gates[index]
            if not gate.active:
                return None
            focus_side = self.gate_focus_side.get(index, "source")
            focus_pos = gate.dst_pos if focus_side == "destination" else gate.enter_pos
            tx, ty = self.level.tilemap_xy(focus_pos)
            return tx * 16 + 8.0, ty * 16 + 8.0
        if kind == "column":
            column = self.level.columns[index]
            if not column.active:
                return None
            tx, ty = self.level.tilemap_xy(column.tilemap_pos)
            return (tx + max(1, column.width) / 2) * 16, (ty + max(1, column.height) / 2) * 16
        if kind == "secret":
            bonus = self.level.bonuses[index]
            tx, ty = self.level.tilemap_xy(bonus.pos)
            return tx * 16 + 8.0, ty * 16 + 8.0
        if kind == "boss":
            boss = self.level.boss
            if not boss.active:
                return None
            return float(boss.x_pos), float(boss.y_pos)
        return None

    def _focus_parsed_object(self, kind: str, index: int) -> None:
        self.selected_parsed_object = (kind, index)
        point = self._object_focus_point(kind, index)
        if point is not None:
            self._center_level_on_world_point(*point)
        self._redraw_level_overlays()

    def _toggle_gate_focus_endpoint(self, index: int) -> None:
        current = self.gate_focus_side.get(index, "source")
        self.gate_focus_side[index] = "destination" if current == "source" else "source"
        self._activate_parsed_row("gate", index)

    def _begin_gate_pick(self, index: int, endpoint: str) -> None:
        self.pending_gate_pick = (index, endpoint)
        self.main_notebook.select(self.level_viewer_tab)
        self.level_side_notebook.select(self.objects_editor_tab)
        self.status_text.set(f"Click a map tile to set Gate {index} {endpoint}.")

    def _apply_gate_pick(self, tile_x: int, tile_y: int) -> bool:
        if self.level is None or self.pending_gate_pick is None:
            return False
        index, endpoint = self.pending_gate_pick
        if not (0 <= index < len(self.level.gates)):
            self.pending_gate_pick = None
            return True
        self._record_undo_state()
        gate = self.level.gates[index]
        pos = (tile_y << 8) | tile_x
        if endpoint == "destination":
            gate.dst_pos = pos
            self.gate_focus_side[index] = "destination"
        else:
            gate.enter_pos = pos
            self.gate_focus_side[index] = "source"
        self.pending_gate_pick = None
        self._refresh_tables()
        self._select_parsed_row_without_camera("gate", index)
        self._redraw_level_overlays()
        self._mark_dirty(f"Gate {index} {endpoint} set to tile ({tile_x}, {tile_y}).")
        return True

    def _select_parsed_row_without_camera(self, kind: str, index: int) -> None:
        self.selected_parsed_object = (kind, index)
        self._refresh_parsed_detail(kind, index)
        self._redraw_level_overlays()

    def _on_parsed_table_selected(self, _event=None) -> None:
        selection = self.tables_tree.selection()
        if not selection:
            return
        iid = selection[0]
        kind_index = self.parsed_table_rows.get(iid)
        if kind_index is None:
            return
        kind, index = kind_index
        self._select_parsed_row_without_camera(kind, index)

    def _on_parsed_table_click(self, event) -> None:
        """Single-click object-list behavior: inspect only, never move the camera.

        Gates still cycle their focused endpoint on repeated single-clicks, but
        the viewport only jumps on an explicit double-click.
        """
        row = self.tables_tree.identify_row(event.y)
        if not row:
            return
        kind_index = self.parsed_table_rows.get(row)
        if kind_index is None:
            return
        kind, index = kind_index
        if kind != "gate":
            return
        if self.selected_parsed_object == ("gate", index):
            current = self.gate_focus_side.get(index, "source")
            self.gate_focus_side[index] = "destination" if current == "source" else "source"
        else:
            self.gate_focus_side[index] = "source"
        self.after_idle(lambda: self._select_parsed_row_without_camera("gate", index))

    def _on_parsed_table_double_click(self, event) -> None:
        """Double-click in the object list is the explicit navigation gesture."""
        row = self.tables_tree.identify_row(event.y)
        if not row:
            return
        kind_index = self.parsed_table_rows.get(row)
        if kind_index is None:
            return
        kind, index = kind_index
        self.after_idle(lambda: self._activate_parsed_row(kind, index))

    def _activate_parsed_row(self, kind: str, index: int) -> None:
        iid = "boss:0" if kind == "boss" else f"{kind}:{index}"
        if iid in self.parsed_table_rows:
            self.tables_tree.selection_set(iid)
            self.tables_tree.focus(iid)
            self.tables_tree.see(iid)
        self._focus_parsed_object(kind, index)
        self._refresh_parsed_detail(kind, index)

    def _monster_parameter_lines(self, monster) -> list[str]:
        labels = {
            "vertical_range_px": "Vertical travel range",
            "vertical_speed_step": "Vertical speed step",
            "activation_x_range_tiles": "Activation X range",
            "activation_y_range_tiles": "Activation Y range",
            "swing_radius_px": "Swing radius",
            "swing_angle_limit": "Swing angle limit",
            "dash_speed_step": "Dash speed step",
            "launch_speed_step": "Launch speed step",
            "jump_up_speed_step": "Jump up speed step",
            "jump_horizontal_speed_step": "Jump horizontal speed step",
            "patrol_left_x_px": "Patrol left bound",
            "patrol_right_x_px": "Patrol right bound",
            "patrol_max_speed_step": "Patrol max speed step",
            "emerge_attack_speed_step": "Emerge attack speed",
            "leap_horizontal_speed_step": "Leap horizontal speed",
            "leap_up_speed_step": "Leap up speed",
            "max_fall_speed_step": "Maximum fall speed",
            "horizontal_speed_step": "Horizontal speed step",
            "movement_pattern_source": "Movement pattern source",
        }
        lines: list[str] = []
        for key, value in monster.extra.items():
            if key.startswith("initial_runtime_") or key == "record_tail_bytes":
                continue
            label = labels.get(key, key.replace("_", " ").title())
            suffix = " tiles" if key.endswith("_tiles") else " px" if key.endswith("_px") else ""
            lines.append(f"  {label}: {value}{suffix}")
        return lines

    def _monster_advanced_lines(self, index: int, monster, sprite_view: int | None) -> list[str]:
        lines = [
            "Advanced / raw:",
            f"  Level record: M{index}",
            f"  Behavior type: T{monster.movement_type}",
            f"  Raw sprite number: {monster.sprite_num_raw}",
            f"  Resolved sprite frame: {sprite_view}",
            f"  Raw flags: 0x{monster.flags:02X}",
            f"  Record length: {monster.length}",
        ]
        for key, value in monster.extra.items():
            if key.startswith("initial_runtime_") or key == "record_tail_bytes":
                lines.append(f"  {key}: {value}")
        return lines

    def _refresh_parsed_detail(self, kind: str, index: int) -> None:
        if self.level is None:
            return
        self._build_parsed_detail_controls(kind, index)
        if kind == "monster" and 0 <= index < len(self.level.monsters):
            monster = self._selected_property_draft_for("monster", index)
            sprite_view = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
            visual_name = monster_visual_name(sprite_view, monster.movement_type)
            title = monster_display_name(sprite_view, monster.behavior_name, monster.movement_type)
            lines = [
                title,
                "",
                f"Enemy visual: {visual_name}",
                f"Behavior: {monster.behavior_name}",
                f"Difficulty: {'Expert only' if monster.expert_only else 'Beginner + Expert'}",
                f"Spawn model: {monster.spawn_mode}",
            ]
            if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                tx, ty, tw, th = monster.trigger_rect_tiles
                lines.append(f"Trigger region: tile x={tx}, y={ty}, width={tw + 1}, height={th + 1}")
            else:
                lines.append(f"Anchor / position: x={monster.x_pos}, y={monster.y_pos} px")
            lines.extend([
                "",
                "Gameplay parameters:",
                f"  Energy: {monster.energy}",
                f"  Respawn / activation ticks: {monster.respawn_ticks}",
                f"  Score value: {monster.score}",
            ])
            param_lines = self._monster_parameter_lines(monster)
            if param_lines:
                lines.extend(param_lines)
            else:
                lines.append("  No extra authored behavior parameters for this type.")
            lines.extend(["", *self._monster_advanced_lines(index, monster, sprite_view)])
            self._render_parsed_detail_sprite(sprite_view, caption=visual_name)
            self._set_parsed_detail_text("\n".join(lines))
            return

        if kind == "item" and 0 <= index < len(self.level.items):
            item = self._selected_property_draft_for("item", index)
            sprite_view = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
            name = item_visual_name(sprite_view)
            lines = [
                name,
                "",
                f"Location: x={item.x_pos}, y={item.y_pos} px",
                f"Y delta / placement tuning: {item.y_delta}",
                "",
                "Advanced / raw:",
                f"  Item slot: {index}",
                f"  Raw sprite number: {item.sprite_num_raw}",
                f"  Resolved sprite frame: {sprite_view}",
            ]
            self._render_parsed_detail_sprite(sprite_view, caption=name)
            self._set_parsed_detail_text("\n".join(lines))
            return

        if kind == "platform" and 0 <= index < len(self.level.platforms):
            platform = self._selected_property_draft_for("platform", index)
            sprite_view = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
            visual_name = platform_visual_name(sprite_view)
            behavior_name = platform_behavior_name(platform.platform_type)
            name = platform_display_name(sprite_view, platform.platform_type)
            extra_lines = [f"  {self._platform_extra_label(platform, key)}: {value}" for key, value in platform.extra.items()]
            lines = [
                name,
                "",
                f"Visual: {visual_name}",
                f"Behavior: {behavior_name}",
                f"Location: x={platform.x_pos}, y={platform.y_pos} px",
                "",
                "Gameplay parameters:",
                *(extra_lines or ["  No additional parsed parameters."]),
                "",
                "Advanced / raw:",
                f"  Platform slot: {index}",
                f"  Raw sprite number: {platform.sprite_num_raw}",
                f"  Resolved sprite frame: {sprite_view}",
                f"  Raw flags: 0x{platform.flags:02X}",
                f"  Raw behavior type: {platform.platform_type}",
            ]
            self._render_parsed_detail_sprite(sprite_view, caption=name)
            self._set_parsed_detail_text("\n".join(lines))
            return

        if kind == "gate" and 0 <= index < len(self.level.gates):
            gate = self._selected_property_draft_for("gate", index)
            enter_xy = self.level.tilemap_xy(gate.enter_pos)
            dst_xy = self.level.tilemap_xy(gate.dst_pos)
            focus_side = self.gate_focus_side.get(index, "source")
            lines = [
                f"Gate {index}",
                "",
                f"Focused endpoint: {focus_side}",
                "Click the same gate row, click the focused endpoint in the map again, or use the button to switch.",
                "",
                f"Source / entry tile: {enter_xy}",
                f"Destination tile: {dst_xy}",
                f"Scroll flag: {gate.scroll_flag}",
                "",
                "Advanced / raw:",
                f"  enter_pos=0x{gate.enter_pos:04X}",
                f"  tilemap_pos=0x{gate.tilemap_pos:04X}",
                f"  dst_pos=0x{gate.dst_pos:04X}",
            ]
            self._render_parsed_detail_sprite(None, caption="Gate")
            self._set_parsed_detail_text("\n".join(lines))
            return

        if kind == "column" and 0 <= index < len(self.level.columns):
            column = self._selected_property_draft_for("column", index)
            map_xy = self.level.tilemap_xy(column.tilemap_pos)
            trigger_xy = self.level.tilemap_xy(column.trigger_pos)
            lines = [
                f"Shifting column {index}",
                "",
                f"Map origin tile: {map_xy}",
                f"Size: {column.width} × {column.height} tiles",
                f"Trigger tile: {trigger_xy}",
                f"Target Y: {column.y_target}",
                "",
                "Advanced / raw:",
                f"  tiles buffer offset=0x{column.tiles_offset_buf:04X}",
                f"  Runtime state / tuning byte: {column.unk9}",
                f"  raw unk9={column.unk9}",
            ]
            self._render_parsed_detail_sprite(None, caption="Shifting column")
            self._set_parsed_detail_text("\n".join(lines))
            return

        if kind == "secret" and 0 <= index < len(self.level.bonuses):
            bonus = self._selected_property_draft_for("secret", index)
            pos_xy = self.level.tilemap_xy(bonus.pos)
            secret_names = {
                "small_random_bonus": "Small random bonus secret",
                "tile_reveal": "Tile reveal / appearing structure secret",
                "big_random_bonus": "Big random bonus secret",
            }
            lines = [
                f"S{index} — {secret_names.get(bonus.mode, bonus.mode)}",
                "",
                f"Position tile: {pos_xy}",
                f"Estimated hit count: {bonus.hit_count_estimate}",
                f"Initial tile: 0x{bonus.tile_num0:02X}",
                f"Revealed tile: 0x{bonus.tile_num1:02X}",
                "",
                "Advanced / raw:",
                f"  Count byte: 0x{bonus.count:02X}",
                f"  Tilemap pos: 0x{bonus.pos:04X}",
            ]
            self._render_secret_detail_tiles(bonus, caption=f"Secret S{index}")
            self._set_parsed_detail_text("\n".join(lines))
            return

        if kind == "boss":
            boss = self._selected_property_draft_for("boss", 0)
            lines = [
                "Boss controller",
                "",
                f"Active: {boss.active}",
                f"Spawn / anchor: x={boss.x_pos}, y={boss.y_pos} px",
                f"Allowed X range: {boss.x_min} .. {boss.x_max}",
                f"Speed: {boss.speed}",
                f"Energy: {boss.energy}",
                f"State: {boss.state}",
            ]
            self._render_parsed_detail_sprite(None, caption="Boss controller")
            self._set_parsed_detail_text("\n".join(lines))
            return

        self._render_parsed_detail_sprite(None)
        self._set_parsed_detail_text("Select an object to inspect it.")

    def _configure_table_tree(self, _columns: list[str]) -> None:
        self.tables_tree.delete(*self.tables_tree.get_children())
        self.parsed_table_rows.clear()
        self.tables_tree["columns"] = ["Object"]
        self.tables_tree.heading("Object", text="Object")
        self.tables_tree.column("Object", width=220, anchor="w", stretch=True)

    def _insert_parsed_row(self, iid: str, kind: str, index: int, label: object) -> None:
        self.parsed_table_rows[iid] = (kind, index)
        self.tables_tree.insert("", "end", iid=iid, values=(label,))

    def _refresh_tables(self) -> None:
        if self.level is None:
            return
        self.selected_parsed_object = None
        selected = self.table_choice.get()
        self._render_parsed_detail_sprite(None)
        self._clear_parsed_detail_controls()
        self._editor_note(
            self.tables_detail_form.inner,
            "Select an object on the left to inspect the mechanics fields that are understood so far.",
        )
        self._set_parsed_detail_text("Select an object on the left to inspect it and focus it in the level editor.")

        if selected == "Monsters":
            cols = ["Enemy", "Behavior", "Difficulty", "Location"]
            self._configure_table_tree(cols)
            expert = self.difficulty.get() == "Expert"
            for idx, monster in enumerate(self.level.monsters):
                if not expert and monster.expert_only:
                    continue
                sprite_view = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
                enemy_name = monster_visual_name(sprite_view, monster.movement_type)
                self._insert_parsed_row(
                    f"monster:{idx}",
                    "monster",
                    idx,
                    f"M{idx}: {enemy_name}",
                )

        elif selected == "Items":
            cols = ["Item", "Location"]
            self._configure_table_tree(cols)
            show_inactive = self.show_inactive_object_slots.get()
            for idx, item in enumerate(self.level.items):
                if not item.active and not show_inactive:
                    continue
                if item.active:
                    sprite_view = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
                    label = f"I{idx}: {item_visual_name(sprite_view)}"
                else:
                    label = f"I{idx}: [inactive item slot]"
                self._insert_parsed_row(
                    f"item:{idx}",
                    "item",
                    idx,
                    label,
                )

        elif selected == "Platforms":
            cols = ["Platform", "Behavior", "Location"]
            self._configure_table_tree(cols)
            show_inactive = self.show_inactive_object_slots.get()
            for idx, platform in enumerate(self.level.platforms):
                if not platform.active and not show_inactive:
                    continue
                if platform.active:
                    sprite_view = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
                    label = f"P{idx}: {platform_visual_name(sprite_view)}"
                else:
                    label = f"P{idx}: [inactive platform slot]"
                self._insert_parsed_row(
                    f"platform:{idx}",
                    "platform",
                    idx,
                    label,
                )

        elif selected == "Gates":
            cols = ["Gate", "Entry", "Destination"]
            self._configure_table_tree(cols)
            show_inactive = self.show_inactive_object_slots.get()
            for idx, gate in enumerate(self.level.gates):
                if not gate.active and not show_inactive:
                    continue
                label = f"G{idx}: Gate" if gate.active else f"G{idx}: [inactive gate slot]"
                self._insert_parsed_row(
                    f"gate:{idx}",
                    "gate",
                    idx,
                    label,
                )

        elif selected == "Columns":
            cols = ["Column", "Location", "Size", "Trigger"]
            self._configure_table_tree(cols)
            show_inactive = self.show_inactive_object_slots.get()
            for idx, column in enumerate(self.level.columns):
                if not column.active and not show_inactive:
                    continue
                label = f"C{idx}: Shifting column" if column.active else f"C{idx}: [inactive column slot]"
                self._insert_parsed_row(
                    f"column:{idx}",
                    "column",
                    idx,
                    label,
                )

        elif selected == "Boss":
            cols = ["Boss", "Location", "Range", "Energy"]
            self._configure_table_tree(cols)
            boss = self.level.boss
            if boss.active or self.show_inactive_object_slots.get():
                self._insert_parsed_row(
                    "boss:0",
                    "boss",
                    0,
                    "Boss controller" if boss.active else "[inactive boss controller]",
                )
        if hasattr(self, "object_catalog_tree") and not self.object_catalog_tree.get_children():
            self._refresh_object_catalog()

    def _refresh_info(self) -> None:
        if self.level is None:
            return
        header = self.level.header
        used_tiles = len(set(self.level.tilemap))
        union_refs = sum(1 for x in self.level.tile_lut if x >= 0x100 and x != 0xFFFF)
        local_refs = sum(1 for x in self.level.tile_lut if x < 0x100)
        animated_groups = self.level.animated_tile_groups
        animated_group_count = len(animated_groups)
        animated_member_count = sum(len(group.members) for group in animated_groups)
        front = sum(1 for x in self.level.tile_attributes2 if x & 0x40)
        attr3_nonzero = sum(1 for x in self.level.tile_attributes3 if x != 0)
        info = f"""Level {self.level.level_id}

Map size:
  tiles: {self.level.width_tiles} × {self.level.height_tiles}
  pixels: {self.level.width_tiles * 16} × {self.level.height_tiles * 16}
  height source: inferred from LEVEL*.SQZ structure and exact metadata EOF

Tile usage:
  tile IDs used in map: {used_tiles}
  local LUT entries: {local_refs}
  UNION LUT entries: {union_refs}

Difficulty preview:
  mode: {self.difficulty.get()}
  monsters visible in this mode: {len(self._visible_monsters())} / {len(self.level.monsters)}
  expert-only monster records: {sum(1 for m in self.level.monsters if m.expert_only)}
  items: unchanged by the difficulty filter in the level-loader logic examined so far

Header parsed from metadata:
  scrolling_top: {header.scrolling_top}
  start_x_pos: {header.start_x_pos}
  start_y_pos: {header.start_y_pos}
  tilemap_w: {header.tilemap_w}
  scrolling_mask: 0x{header.scrolling_mask:02X}

Tile behavior tables:
  animated tile groups: {animated_group_count} groups / {animated_member_count} tile phases
  animated tile playback: {'on' if self.animate_tiles.get() else 'off'} ({self.animation_frame_label.get()})
  front-mask tile definitions: {front}
  tiles with non-default floor profile / slopes: {attr3_nonzero}
  collision split: sides=table 0, top surface=table 1, underside+dynamic flags=table 2, floor profile=table 3

Parsed metadata tables:
  gates active: {len(self.level.active_gates)} / {len(self.level.gates)}
  shifting columns active: {len(self.level.active_columns)} / {len(self.level.columns)}
  monsters: {len(self.level.monsters)}
  secrets active: {len(self.level.active_bonuses)} / {len(self.level.bonuses)}
  items active: {len(self.level.active_items)} / {len(self.level.items)}
  platforms active: {len(self.level.active_platforms)} / {len(self.level.platforms)}
  boss active: {self.level.boss.active}

Sprite number bases, raw from level:
  items offset: {self.level.items_sprite_num_offset}
  monsters offset: {self.level.monsters_sprite_num_offset}

Sprite visualizer:
  SPRITES.SQZ decoded: {len(self.sprites_blob)} bytes
  sprite geometry frames: {self.sprite_tables.count}
  geometry source: temporary RE bootstrap table; target is direct PRE2.EXE extraction

Raw offsets:
  decompressed level size: {len(self.level.raw)} bytes
  tile graphics start: 0x{self.level.tiles_blob_offset:04X}
  local tile count: {self.level.local_tiles_count}
  level metadata start: 0x{self.level.metadata_offset:04X}
  monster attr region: 0x{self.level.monster_attr_region_offset:04X}–0x{self.level.monster_attr_region_end:04X}
  metadata end after boss block: 0x{self.level.metadata_end_offset:04X}
"""
        self._set_info_text(info)

    def _set_info_text(self, text: str) -> None:
        self.info_box.configure(state="normal")
        self.info_box.delete("1.0", "end")
        self.info_box.insert("1.0", text)
        self.info_box.configure(state="disabled")

    def _level_coords_from_event(self, event) -> tuple[int, int] | None:
        if self.level is None:
            return None
        x = int(self.level_canvas.canvas.canvasx(event.x))
        y = int(self.level_canvas.canvas.canvasy(event.y))
        scale = self.zoom.get()
        tx = x // (16 * scale)
        ty = y // (16 * scale)
        if 0 <= tx < self.level.width_tiles and 0 <= ty < self.level.height_tiles:
            return tx, ty
        return None

    def _tile_eraser_num(self) -> int:
        """Best-effort empty/background tile for right-button erasing.

        Prehistorik 2 maps are fully tiled and many levels use a level-specific
        background/filler tile instead of a universal tile 0. The dominant tile
        in the map is usually the natural empty canvas for that level, which
        makes the eraser behave like "clear to background" across tilesets.
        """
        if self.level is None or not self.level.tilemap:
            return 0
        return Counter(self.level.tilemap).most_common(1)[0][0]

    def _begin_tile_paint_stroke(self, event, *, erase: bool) -> bool:
        if self.level is None or self.editor_tool.get() != "Place" or self._active_level_editor_tab() != "Tiles":
            return False
        if not erase and self.selected_tile_catalog_num is None:
            self.status_text.set("Pick a tile from Tile Catalog first; then drag on the level to paint it.")
            if hasattr(self, "tiles_tool_tabs"):
                self.tiles_tool_tabs.select(0)
            return True
        # Keep the serialized map mutable for one brush stroke. Per-cell previews
        # appear immediately, while the persistent map bitmap refreshes in cached
        # chunks on idle; this avoids both per-cell and mouse-up full-level renders.
        mutable_tilemap = bytearray(self.level.tilemap)
        self.level.tilemap = mutable_tilemap
        self._tile_paint_preview_photos.clear()
        self.level_canvas.canvas.delete("tile_paint_preview")
        self._tile_paint_state = {
            "erase": erase,
            "tile_num": self._tile_eraser_num() if erase else self.selected_tile_catalog_num,
            "last_coords": None,
            "changed": False,
            "tilemap": mutable_tilemap,
            "preview_items": {},
            "preview_base_tile": None,
            "preview_front_tile": None,
        }
        self._tile_paint_pre_edit_snapshot = copy.deepcopy(self.level)
        if self._tile_paint_pre_edit_snapshot is not None:
            # Keep undo snapshots canonical even though the live level uses a
            # temporary bytearray during the stroke.
            self._tile_paint_pre_edit_snapshot.tilemap = bytes(self._tile_paint_pre_edit_snapshot.tilemap)
        self._paint_tile_stroke_at_event(event)
        return True

    def _tile_paint_preview_backdrop(self, tx: int, ty: int, tile_px: int) -> Image.Image:
        """Return an opaque viewport-background crop for one locally repainted tile."""
        bg = self.level_canvas.viewport_background_resized
        canvas = self.level_canvas.canvas
        if bg is None:
            return Image.new("RGBA", (tile_px, tile_px), (0, 0, 0, 255))
        screen_x = int(tx * tile_px - canvas.canvasx(0))
        screen_y = int(ty * tile_px - canvas.canvasy(0))
        patch = Image.new("RGBA", (tile_px, tile_px), (0, 0, 0, 255))
        crop_x0 = max(0, screen_x)
        crop_y0 = max(0, screen_y)
        crop_x1 = min(bg.width, screen_x + tile_px)
        crop_y1 = min(bg.height, screen_y + tile_px)
        if crop_x0 < crop_x1 and crop_y0 < crop_y1:
            crop = bg.crop((crop_x0, crop_y0, crop_x1, crop_y1)).convert("RGBA")
            patch.alpha_composite(crop, (crop_x0 - screen_x, crop_y0 - screen_y))
        return patch

    def _draw_tile_paint_preview(self, tx: int, ty: int, tile_num: int) -> None:
        """Locally repaint one edited map cell without rebuilding the full level image."""
        if self.level is None or not self.union_tiles or self._tile_paint_state is None:
            return
        scale = max(1, self.zoom.get())
        tile_px = 16 * scale
        patch = self._tile_paint_preview_backdrop(tx, ty, tile_px)
        base_tile = self._tile_paint_state.get("preview_base_tile")
        if not isinstance(base_tile, Image.Image):
            base_tile = render_tile_image(
                self.level,
                self.union_tiles,
                self.palettes[self.current_level_index],
                tile_num,
                scale=scale,
                transparent_zero=True,
            ).convert("RGBA")
            self._tile_paint_state["preview_base_tile"] = base_tile
        patch.alpha_composite(base_tile)
        if self.front_enabled.get() and self.front_tiles and self.level.tile_attributes2[tile_num] & 0x40:
            # Front-mask tiles are part of the base level raster when this preview
            # layer is enabled; keep the local replacement visually consistent.
            front = self._tile_paint_state.get("preview_front_tile")
            if not isinstance(front, Image.Image):
                from pre2lib.renderer import render_front_tile_image
                front = render_front_tile_image(
                    self.level,
                    self.front_tiles,
                    self.palettes[self.current_level_index],
                    tile_num,
                    scale=scale,
                ).convert("RGBA")
                self._tile_paint_state["preview_front_tile"] = front
            # Palette index 0 is not transparent in render_front_tile_image, so use
            # the cheap full preview here; the final release render remains exact.
            patch.alpha_composite(front)
        photo = ImageTk.PhotoImage(patch)
        coords = (tx, ty)
        preview_items = self._tile_paint_state.get("preview_items")
        if isinstance(preview_items, dict):
            old_item = preview_items.get(coords)
            if old_item is not None:
                self.level_canvas.canvas.delete(old_item)
            item = self.level_canvas.canvas.create_image(
                tx * tile_px,
                ty * tile_px,
                image=photo,
                anchor="nw",
                tags=("tile_paint_preview",),
            )
            preview_items[coords] = item
            if self.level_canvas.canvas.find_withtag("animated_tile_overlay"):
                self.level_canvas.canvas.tag_lower(item, "animated_tile_overlay")
            elif self.level_canvas.canvas.find_withtag("overlay"):
                self.level_canvas.canvas.tag_lower(item, "overlay")
            else:
                self.level_canvas.canvas.tag_raise(item)
        self._tile_paint_preview_photos[coords] = photo

    def _paint_tile_stroke_at_event(self, event) -> bool:
        if self.level is None or self._tile_paint_state is None:
            return False
        coords = self._level_coords_from_event(event)
        if coords is None:
            return True
        if self._tile_paint_state.get("last_coords") == coords:
            return True
        self._tile_paint_state["last_coords"] = coords
        tx, ty = coords
        erase = bool(self._tile_paint_state.get("erase"))
        tile_num = self._tile_paint_state.get("tile_num")
        mutable_tilemap = self._tile_paint_state.get("tilemap")
        if tile_num is None or not isinstance(mutable_tilemap, bytearray):
            return True
        offset = ty * self.level.width_tiles + tx
        if not (0 <= offset < len(mutable_tilemap)) or mutable_tilemap[offset] == tile_num:
            return True
        mutable_tilemap[offset] = int(tile_num)
        self._tile_paint_state["changed"] = True
        self.dirty = True
        self._draw_tile_paint_preview(tx, ty, int(tile_num))
        self._queue_level_chunk_refresh(tx, ty)
        self.tile_info_text.set(
            f"{'Erased' if erase else 'Painted'} map ({tx}, {ty}) → tile 0x{int(tile_num):02X}"
        )
        return True

    def _finish_tile_paint_stroke(self) -> bool:
        if self._tile_paint_state is None:
            return False
        state = self._tile_paint_state
        self._tile_paint_state = None
        mutable_tilemap = state.get("tilemap")
        if self.level is not None and isinstance(mutable_tilemap, bytearray):
            self.level.tilemap = bytes(mutable_tilemap)
        if bool(state.get("changed")):
            if self._tile_paint_pre_edit_snapshot is not None:
                self.undo_stack.append(self._tile_paint_pre_edit_snapshot)
                if len(self.undo_stack) > 100:
                    self.undo_stack.pop(0)
                self.redo_stack.clear()
            stroke_tile = int(state.get("tile_num") or 0)
            if bool(state.get("erase")):
                self._mark_dirty(f"Erased tiles to inferred empty tile 0x{stroke_tile:02X}.")
            else:
                self._mark_dirty(f"Painted tile 0x{stroke_tile:02X}.")
            # Any queued chunks are local, not a whole-level rebuild; flush now so the
            # saved visual state is exact even if the event loop has not gone idle yet.
            self._flush_dirty_level_chunks()
            self.level_canvas.canvas.delete("tile_paint_preview")
            self._tile_paint_preview_photos.clear()
            self._refresh_info()
        else:
            self.level_canvas.canvas.delete("tile_paint_preview")
        self._tile_paint_preview_photos.clear()
        self._tile_paint_pre_edit_snapshot = None
        return True

    def _on_level_right_press(self, event) -> None:
        self._begin_tile_paint_stroke(event, erase=True)

    def _on_level_right_drag(self, event) -> None:
        self._paint_tile_stroke_at_event(event)
        self._on_level_motion(event)

    def _on_level_right_release(self, _event) -> None:
        self._finish_tile_paint_stroke()

    def _on_level_press(self, event) -> None:
        if self._begin_tile_paint_stroke(event, erase=False):
            self._level_pan_state = None
            self._object_drag_state = None
            return
        self._level_pan_state = {"x": event.x, "y": event.y, "dragged": False}
        self._object_drag_state = None
        if (
            self.editor_tool.get() == "Select"
            and self._active_level_editor_tab() == "Objects"
            and self.pending_gate_pick is None
        ):
            resize_hit = self._selected_trigger_resize_handle_from_event(event)
            if resize_hit is not None:
                index, handle, trigger = resize_hit
                self._object_drag_state = {
                    "kind": "monster",
                    "index": index,
                    "resize_handle": handle,
                    "initial_trigger": trigger,
                }
                press_world = self._world_point_from_event(event)
                if press_world is not None:
                    self._object_drag_state["press_world"] = press_world
                self._drag_pre_edit_snapshot = copy.deepcopy(self.level) if self.level is not None else None
                return
            object_hit = self._object_hit_from_event(event)
            if object_hit is not None:
                kind, index, gate_side = object_hit
                if kind == "gate" and gate_side is not None:
                    self.gate_focus_side[index] = gate_side
                self._open_parsed_object(kind, index, center=False)
                drag_state: dict[str, object] = {"kind": kind, "index": index}
                press_world = self._world_point_from_event(event)
                if press_world is not None:
                    drag_state["press_world"] = press_world
                drag_origin = self._object_drag_origin(kind, index)
                if drag_origin is not None:
                    drag_state["origin"] = drag_origin
                self._object_drag_state = drag_state
                self._drag_pre_edit_snapshot = copy.deepcopy(self.level) if self.level is not None else None
                return
        if self.editor_tool.get() == "View":
            self.level_canvas.canvas.scan_mark(event.x, event.y)

    def _on_level_double_click(self, event) -> None:
        """Promote a passive View double-click into a Select-mode inspection.

        Prefer parsed gameplay objects when one is under the cursor; otherwise the
        map tile becomes the selection. This mirrors the editor's "View = browse,
        Select = inspect/edit" split without forcing users to switch tools first.
        """
        if self.editor_tool.get() != "View":
            return
        self.editor_tool.set("Select")
        self._on_editor_tool_changed()
        object_hit = self._object_hit_from_event(event, visible_overlays_only=True)
        if object_hit is not None:
            kind, index, gate_side = object_hit
            if kind == "gate" and gate_side is not None:
                self.gate_focus_side[index] = gate_side
            self.level_side_notebook.select(self.objects_editor_tab)
            self._open_parsed_object(kind, index, center=False)
            self.status_text.set(f"Selected {kind} {index} from View double-click.")
            return
        self.level_side_notebook.select(self.tiles_editor_tab)
        self._select_level_tile(event)

    def _on_level_drag(self, event) -> None:
        if self._tile_paint_state is not None:
            self._paint_tile_stroke_at_event(event)
            self._on_level_motion(event)
            return
        if self._level_pan_state is None:
            return
        dx = abs(event.x - int(self._level_pan_state["x"]))
        dy = abs(event.y - int(self._level_pan_state["y"]))
        if dx > 3 or dy > 3:
            self._level_pan_state["dragged"] = True
        if self._object_drag_state is not None:
            self._move_selected_object_to_event(event, quiet=True)
        elif self.editor_tool.get() == "View":
            self.level_canvas.canvas.scan_dragto(event.x, event.y, gain=1)
            self.level_canvas.position_viewport_background()
            self._redraw_animated_tile_overlay()
        self._on_level_motion(event)

    def _on_level_release(self, event) -> None:
        if self._finish_tile_paint_stroke():
            return
        state = self._level_pan_state
        object_drag = self._object_drag_state
        self._level_pan_state = None
        self._object_drag_state = None
        if object_drag is not None:
            if state is not None and bool(state["dragged"]):
                kind = object_drag["kind"]
                index = object_drag["index"]
                resized_trigger = bool(object_drag.get("resize_handle"))
                if self._drag_pre_edit_snapshot is not None:
                    self.undo_stack.append(self._drag_pre_edit_snapshot)
                    if len(self.undo_stack) > 100:
                        self.undo_stack.pop(0)
                    self.redo_stack.clear()
                action = "Resized trigger region for" if resized_trigger else "Moved"
                self._mark_dirty(f"{action} {kind} {index}.")
                self._refresh_tables()
                self._select_parsed_row_without_camera(kind, index)
                self._refresh_info()
            self._drag_pre_edit_snapshot = None
            return
        if state is not None and (self.editor_tool.get() != "View" or not bool(state["dragged"])):
            self._select_level_at_event(event)

    def _first_inactive_slot(self, rows: list[object]) -> int | None:
        for index, row in enumerate(rows):
            if not getattr(row, "active", False):
                return index
        return None

    def _place_item_at(self, world_x: float, world_y: float) -> None:
        if self.level is None:
            return
        slot = self._first_inactive_slot(self.level.items)
        if slot is None:
            self.status_text.set("No free item slots in this level.")
            return
        template = self._draft_object("item")
        if template is None:
            template = next((item for item in self.level.items if item.active), None)
        self._record_undo_state()
        item = self.level.items[slot]
        item.x_pos = int(world_x)
        item.y_pos = int(world_y)
        item.sprite_num_raw = template.sprite_num_raw if template is not None else 0
        item.y_delta = template.y_delta if template is not None else 0
        self._mark_dirty(f"Placed item I{slot}.")
        self._refresh_tables()
        self._select_parsed_row_without_camera("item", slot)

    def _place_monster_at(self, world_x: float, world_y: float, tile_x: int, tile_y: int) -> None:
        if self.level is None:
            return
        template = self._draft_object("monster")
        if template is None:
            self.status_text.set("Pick a monster type from the Objects placement catalog first.")
            return
        self._record_undo_state()
        monster = copy.deepcopy(template)
        monster.raw_offset = -1
        monster.length = EXPECTED_MONSTER_LENGTHS.get(monster.movement_type, monster.length)
        if monster.uses_trigger_rect:
            _old_tx, _old_ty, tw, th = monster.trigger_rect_tiles or (0, 0, 0, 0)
            monster.x_pos = (tile_y << 8) | tile_x
            monster.y_pos = (th << 8) | tw
        else:
            monster.x_pos = int(world_x)
            monster.y_pos = int(world_y)
        self.level.monsters.append(monster)
        slot = len(self.level.monsters) - 1
        self._mark_dirty(f"Placed monster M{slot}. Monster records will be compacted/rebuilt on save.")
        self._refresh_tables()
        self._select_parsed_row_without_camera("monster", slot)

    def _place_platform_at(self, world_x: float, world_y: float) -> None:
        if self.level is None:
            return
        slot = self._first_inactive_slot(self.level.platforms)
        if slot is None:
            self.status_text.set("No free platform slots in this level.")
            return
        template = self._draft_object("platform")
        if template is None:
            template = next((platform for platform in self.level.platforms if platform.active), None)
        self._record_undo_state()
        platform = self.level.platforms[slot]
        platform.x_pos = int(world_x)
        platform.y_pos = int(world_y)
        if template is not None:
            platform.sprite_num_raw = template.sprite_num_raw
            platform.flags = template.flags
            platform.variant = template.variant
            platform.extra = dict(template.extra)
        else:
            platform.sprite_num_raw = 0
            platform.flags = 2
            platform.variant = "other"
            platform.extra = {
                "max_velocity": 1,
                "padding": 0,
                "unk9": 0,
                "unkA": 48,
                "counter": 0,
                "velocity": 0,
            }
        self._mark_dirty(f"Placed platform P{slot}.")
        self._refresh_tables()
        self._select_parsed_row_without_camera("platform", slot)

    def _place_gate_at(self, tile_x: int, tile_y: int) -> None:
        if self.level is None:
            return
        slot = self._first_inactive_slot(self.level.gates)
        if slot is None:
            self.status_text.set("No free gate slots in this level.")
            return
        self._record_undo_state()
        pos = (tile_y << 8) | tile_x
        gate = self.level.gates[slot]
        gate.enter_pos = pos
        gate.tilemap_pos = pos
        gate.dst_pos = pos
        draft_gate = self._draft_object("gate")
        gate.scroll_flag = draft_gate.scroll_flag if draft_gate is not None else 0
        self.gate_focus_side[slot] = "source"
        self.pending_gate_pick = (slot, "destination")
        self._mark_dirty(f"Placed gate G{slot} source. Click another map tile to set its destination.")
        self._refresh_tables()
        self._select_parsed_row_without_camera("gate", slot)

    def _place_tile_at_event(self, event) -> bool:
        if self.level is None or self.selected_tile_catalog_num is None:
            self.status_text.set("Pick a tile from Tile Catalog before using the tile paint brush.")
            if hasattr(self, "tiles_tool_tabs"):
                self.tiles_tool_tabs.select(0)
            return True
        coords = self._level_coords_from_event(event)
        if coords is None:
            return True
        tx, ty = coords
        self._record_undo_state()
        tilemap = bytearray(self.level.tilemap)
        tilemap[ty * self.level.width_tiles + tx] = self.selected_tile_catalog_num
        self.level.tilemap = bytes(tilemap)
        self._mark_dirty(f"Placed tile 0x{self.selected_tile_catalog_num:02X} at ({tx}, {ty}).")
        self._show_tile_properties(self.selected_tile_catalog_num, map_xy=(tx, ty), source="Tile Place")
        self._queue_level_chunk_refresh(tx, ty)
        self._flush_dirty_level_chunks()
        return True

    def _object_drag_origin(self, kind: str, index: int) -> tuple[str, float, float] | None:
        if self.level is None:
            return None
        if kind == "monster" and 0 <= index < len(self.level.monsters):
            monster = self.level.monsters[index]
            if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                tx, ty, _tw, _th = monster.trigger_rect_tiles
                return "tile", float(tx), float(ty)
            return "world", float(monster.x_pos), float(monster.y_pos)
        if kind == "column" and 0 <= index < len(self.level.columns):
            tx, ty = self.level.tilemap_xy(self.level.columns[index].tilemap_pos)
            return "tile", float(tx), float(ty)
        if kind == "boss":
            return "world", float(self.level.boss.x_pos), float(self.level.boss.y_pos)
        if kind == "item" and 0 <= index < len(self.level.items):
            item = self.level.items[index]
            return "world", float(item.x_pos), float(item.y_pos)
        if kind == "platform" and 0 <= index < len(self.level.platforms):
            platform = self.level.platforms[index]
            return "world", float(platform.x_pos), float(platform.y_pos)
        if kind == "gate" and 0 <= index < len(self.level.gates):
            gate = self.level.gates[index]
            pos = gate.dst_pos if self.gate_focus_side.get(index, "source") == "destination" else gate.enter_pos
            tx, ty = self.level.tilemap_xy(pos)
            return "tile", float(tx), float(ty)
        if kind == "secret" and 0 <= index < len(self.level.bonuses):
            tx, ty = self.level.tilemap_xy(self.level.bonuses[index].pos)
            return "tile", float(tx), float(ty)
        return None

    def _relative_drag_target(self, event, kind: str, index: int) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        world = self._world_point_from_event(event)
        coords = self._level_coords_from_event(event)
        drag = self._object_drag_state or {}
        press_world = drag.get("press_world")
        origin = drag.get("origin")
        if (
            world is None
            or not isinstance(press_world, tuple)
            or len(press_world) != 2
            or not isinstance(origin, tuple)
            or len(origin) != 3
        ):
            if world is None:
                world_int = None
            else:
                wx, wy = int(world[0]), int(world[1])
                if self.level is not None:
                    wx = max(0, min(self.level.width_tiles * 16 - 1, wx))
                    wy = max(0, min(self.level.height_tiles * 16 - 1, wy))
                world_int = (wx, wy)
            return world_int, coords

        origin_domain, origin_x, origin_y = origin
        delta_x = world[0] - float(press_world[0])
        delta_y = world[1] - float(press_world[1])
        if origin_domain == "world":
            wx = int(round(float(origin_x) + delta_x))
            wy = int(round(float(origin_y) + delta_y))
            if self.level is not None:
                # Prevent an imprecise grab near a large sprite's edge from
                # throwing its anchor far outside the editable level bounds.
                wx = max(0, min(self.level.width_tiles * 16 - 1, wx))
                wy = max(0, min(self.level.height_tiles * 16 - 1, wy))
            return (wx, wy), coords
        if origin_domain == "tile":
            tx = int(round(float(origin_x) + delta_x / 16.0))
            ty = int(round(float(origin_y) + delta_y / 16.0))
            if self.level is None:
                return None, None
            tx = max(0, min(self.level.width_tiles - 1, tx))
            ty = max(0, min(self.level.height_tiles - 1, ty))
            return None, (tx, ty)
        if world is None:
            world_int = None
        else:
            wx, wy = int(world[0]), int(world[1])
            if self.level is not None:
                wx = max(0, min(self.level.width_tiles * 16 - 1, wx))
                wy = max(0, min(self.level.height_tiles * 16 - 1, wy))
            world_int = (wx, wy)
        return world_int, coords

    def _resize_selected_trigger_monster_to_event(self, event) -> bool:
        if self.level is None or self.selected_parsed_object is None or self._object_drag_state is None:
            return False
        kind, index = self.selected_parsed_object
        drag = self._object_drag_state
        if kind != "monster" or drag.get("resize_handle") is None or not 0 <= index < len(self.level.monsters):
            return False
        monster = self.level.monsters[index]
        trigger = drag.get("initial_trigger")
        point = self._world_point_from_event(event)
        if not isinstance(trigger, tuple) or len(trigger) != 4 or point is None:
            return False
        tx, ty, tw, th = (int(trigger[0]), int(trigger[1]), int(trigger[2]), int(trigger[3]))
        left = tx
        top = ty
        right = tx + tw + 1
        bottom = ty + th + 1
        edge_x = int(round(point[0] / 16.0))
        edge_y = int(round(point[1] / 16.0))
        edge_x = max(0, min(self.level.width_tiles, edge_x))
        edge_y = max(0, min(self.level.height_tiles, edge_y))
        handle = str(drag.get("resize_handle"))
        if "w" in handle:
            left = min(edge_x, right - 1)
        if "e" in handle:
            right = max(edge_x, left + 1)
        if "n" in handle:
            top = min(edge_y, bottom - 1)
        if "s" in handle:
            bottom = max(edge_y, top + 1)
        left = max(0, min(self.level.width_tiles - 1, left))
        top = max(0, min(self.level.height_tiles - 1, top))
        right = max(left + 1, min(self.level.width_tiles, right))
        bottom = max(top + 1, min(self.level.height_tiles, bottom))
        width_minus_one = right - left - 1
        height_minus_one = bottom - top - 1
        monster.x_pos = (top << 8) | left
        monster.y_pos = ((height_minus_one & 0xFF) << 8) | (width_minus_one & 0xFF)
        self.selected_parsed_object = ("monster", index)
        self._redraw_level_overlays()
        return True

    def _move_selected_object_to_event(self, event, *, quiet: bool = False) -> bool:
        if self._resize_selected_trigger_monster_to_event(event):
            return True
        if self.level is None or self.selected_parsed_object is None:
            self.status_text.set("Select an object before dragging it.")
            return True
        kind, index = self.selected_parsed_object
        world_target, coords = self._relative_drag_target(event, kind, index)
        if coords is None:
            return True
        tx, ty = coords
        wx, wy = world_target if world_target is not None else (tx * 16 + 8, ty * 16 + 8)
        if kind == "monster" and 0 <= index < len(self.level.monsters):
            monster = self.level.monsters[index]
            if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                _old_tx, _old_ty, tw, th = monster.trigger_rect_tiles
                monster.x_pos = (ty << 8) | tx
                monster.y_pos = (th << 8) | tw
            else:
                monster.x_pos = wx
                monster.y_pos = wy
        elif kind == "column" and 0 <= index < len(self.level.columns):
            self.level.columns[index].tilemap_pos = (ty << 8) | tx
        elif kind == "boss":
            self.level.boss.x_pos = wx
            self.level.boss.y_pos = wy
        elif kind == "item" and 0 <= index < len(self.level.items):
            self.level.items[index].x_pos = wx
            self.level.items[index].y_pos = wy
        elif kind == "platform" and 0 <= index < len(self.level.platforms):
            self.level.platforms[index].x_pos = wx
            self.level.platforms[index].y_pos = wy
        elif kind == "gate" and 0 <= index < len(self.level.gates):
            gate = self.level.gates[index]
            pos = (ty << 8) | tx
            if self.gate_focus_side.get(index, "source") == "destination":
                gate.dst_pos = pos
            else:
                gate.enter_pos = pos
        elif kind == "secret" and 0 <= index < len(self.level.bonuses):
            self.level.bonuses[index].pos = (ty << 8) | tx
        else:
            self.status_text.set(f"Drag reposition is not implemented for {kind}.")
            return True
        # Keep the selected object highlighted while dragging or after a direct
        # move, but never recenter the map. Moving an object must not turn the
        # camera into a follower.
        self.selected_parsed_object = (kind, index)
        if not quiet:
            self._refresh_tables()
            self._refresh_parsed_detail(kind, index)
        self._redraw_level_overlays()
        if not quiet:
            self._mark_dirty(f"Moved {kind} {index}.")
        return True

    def _place_object_at_event(self, event) -> bool:
        if self.editor_tool.get() != "Place" or self._active_level_editor_tab() != "Objects":
            return False
        tool = self.object_tool.get()
        world = self._world_point_from_event(event)
        coords = self._level_coords_from_event(event)
        if world is None or coords is None:
            return True
        if tool == "Place item":
            if not self.placement_catalog_selection or self.placement_catalog_selection[0] != "item":
                self.status_text.set("Pick an item type from the Objects placement catalog first.")
                self.main_notebook.select(self.level_viewer_tab)
                self.level_side_notebook.select(self.objects_editor_tab)
                if hasattr(self, "object_tool_tabs"):
                    self.object_tool_tabs.select(1)
                return True
            self._place_item_at(*world)
        elif tool == "Place monster":
            if not self.placement_catalog_selection or self.placement_catalog_selection[0] != "monster":
                self.status_text.set("Pick a monster type from the Objects placement catalog first.")
                self.main_notebook.select(self.level_viewer_tab)
                self.level_side_notebook.select(self.objects_editor_tab)
                if hasattr(self, "object_tool_tabs"):
                    self.object_tool_tabs.select(1)
                return True
            self._place_monster_at(world[0], world[1], coords[0], coords[1])
        elif tool == "Place platform":
            if not self.placement_catalog_selection or self.placement_catalog_selection[0] != "platform":
                self.status_text.set("Pick a platform type from the Objects placement catalog first.")
                self.main_notebook.select(self.level_viewer_tab)
                self.level_side_notebook.select(self.objects_editor_tab)
                if hasattr(self, "object_tool_tabs"):
                    self.object_tool_tabs.select(1)
                return True
            self._place_platform_at(*world)
        elif tool == "Place gate":
            self._place_gate_at(*coords)
        self._redraw_level_overlays()
        return True

    def _select_level_at_event(self, event) -> None:
        # View is deliberately non-destructive and non-inspecting: a normal
        # click only belongs to panning/navigation. Double-click is the explicit
        # "inspect/select what is here" gesture handled separately below.
        if self.editor_tool.get() == "View":
            return
        coords = self._level_coords_from_event(event)
        if coords is not None and self._apply_gate_pick(*coords):
            return
        if self.editor_tool.get() == "Place" and self._active_level_editor_tab() == "Tiles":
            self._place_tile_at_event(event)
            return
        if self._place_object_at_event(event):
            return
        if self.editor_tool.get() == "Select" and self._active_level_editor_tab() == "Objects":
            object_hit = self._object_hit_from_event(event)
            if object_hit is not None:
                kind, index, gate_side = object_hit
                if kind == "gate" and gate_side is not None:
                    current = self.gate_focus_side.get(index, "source")
                    if self.selected_parsed_object == ("gate", index) and current == gate_side:
                        self.gate_focus_side[index] = "destination" if current == "source" else "source"
                    else:
                        self.gate_focus_side[index] = gate_side
                self._open_parsed_object(kind, index, center=False)
            return
        if self.editor_tool.get() == "Select" and self._active_level_editor_tab() == "Tiles":
            self._select_level_tile(event)
            return

    def _world_point_from_event(self, event) -> tuple[float, float] | None:
        if self.level is None:
            return None
        scale = max(1, self.zoom.get())
        canvas_x = self.level_canvas.canvas.canvasx(event.x)
        canvas_y = self.level_canvas.canvas.canvasy(event.y)
        return canvas_x / scale, canvas_y / scale

    def _sprite_world_bbox(self, sprite_num: int | None, world_x: float, world_y: float) -> tuple[float, float, float, float] | None:
        if sprite_num is None or not 0 <= sprite_num < self.sprite_tables.count:
            return None
        try:
            width, height = self.sprite_tables.size(sprite_num)
            origin_x, _origin_y = self.sprite_tables.origin(sprite_num)
        except Exception:
            return None
        return world_x - origin_x, world_y - height, world_x - origin_x + width, world_y

    @staticmethod
    def _point_in_rect(point_x: float, point_y: float, rect: tuple[float, float, float, float], pad: float = 0.0) -> bool:
        x0, y0, x1, y1 = rect
        return x0 - pad <= point_x <= x1 + pad and y0 - pad <= point_y <= y1 + pad

    def _selected_trigger_resize_handle_from_event(self, event) -> tuple[int, str, tuple[int, int, int, int]] | None:
        """Return the selected trigger-monster corner under the cursor, if any."""
        if self.level is None or self.selected_parsed_object is None:
            return None
        kind, index = self.selected_parsed_object
        if kind != "monster" or not 0 <= index < len(self.level.monsters):
            return None
        monster = self.level.monsters[index]
        if not monster.uses_trigger_rect or monster.trigger_rect_tiles is None:
            return None
        point = self._world_point_from_event(event)
        if point is None:
            return None
        wx, wy = point
        tx, ty, tw, th = monster.trigger_rect_tiles
        left = float(tx * 16)
        top = float(ty * 16)
        right = float((tx + tw + 1) * 16)
        bottom = float((ty + th + 1) * 16)
        scale = max(1, self.zoom.get())
        pad = max(3.0, 8.0 / float(scale))
        handles = {
            "nw": (left, top),
            "ne": (right, top),
            "sw": (left, bottom),
            "se": (right, bottom),
        }
        for handle, (hx, hy) in handles.items():
            if abs(wx - hx) <= pad and abs(wy - hy) <= pad:
                return index, handle, (tx, ty, tw, th)
        return None

    def _object_hit_from_event(self, event, *, visible_overlays_only: bool = True) -> tuple[str, int, str | None] | None:
        """Return a parsed object under the click.

        Select-mode clicks and View-mode double-clicks respect the visible overlay
        switches.  Hidden editor layers therefore do not unexpectedly steal a hit
        from the tile or object layer the user is currently looking at.
        """
        if self.level is None:
            return None
        point = self._world_point_from_event(event)
        if point is None:
            return None
        wx, wy = point

        if not visible_overlays_only or self.overlay_items.get():
            for idx, item in enumerate(self.level.items):
                if not item.active:
                    continue
                spr = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
                bbox = self._sprite_world_bbox(spr, item.x_pos, item.y_pos)
                if bbox is not None and self._point_in_rect(wx, wy, bbox, pad=3.0):
                    return "item", idx, None

        if not visible_overlays_only or self.overlay_monsters.get():
            expert = self.difficulty.get() == "Expert"
            for idx, monster in enumerate(self.level.monsters):
                if not expert and monster.expert_only:
                    continue
                if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                    tx, ty, tw, th = monster.trigger_rect_tiles
                    bbox = (tx * 16, ty * 16, (tx + tw + 1) * 16, (ty + th + 1) * 16)
                    if self._point_in_rect(wx, wy, bbox):
                        return "monster", idx, None
                else:
                    spr = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
                    bbox = self._sprite_world_bbox(spr, monster.x_pos, monster.y_pos)
                    if bbox is not None and self._point_in_rect(wx, wy, bbox, pad=4.0):
                        return "monster", idx, None

        if not visible_overlays_only or self.overlay_platforms.get():
            for idx, platform in enumerate(self.level.platforms):
                if not platform.active:
                    continue
                spr = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
                bbox = self._sprite_world_bbox(spr, platform.x_pos, platform.y_pos)
                if bbox is not None and self._point_in_rect(wx, wy, bbox, pad=4.0):
                    return "platform", idx, None

        if not visible_overlays_only or self.overlay_gates.get():
            for idx, gate in enumerate(self.level.gates):
                if not gate.active:
                    continue
                source_xy = self.level.tilemap_xy(gate.enter_pos)
                dest_xy = self.level.tilemap_xy(gate.dst_pos)
                source_rect = (source_xy[0] * 16, source_xy[1] * 16, (source_xy[0] + 1) * 16, (source_xy[1] + 1) * 16)
                dest_rect = (dest_xy[0] * 16, dest_xy[1] * 16, (dest_xy[0] + 1) * 16, (dest_xy[1] + 1) * 16)
                if self._point_in_rect(wx, wy, source_rect):
                    return "gate", idx, "source"
                if self._point_in_rect(wx, wy, dest_rect):
                    return "gate", idx, "destination"

        if not visible_overlays_only or self.overlay_columns.get():
            for idx, column in enumerate(self.level.columns):
                if not column.active:
                    continue
                tx, ty = self.level.tilemap_xy(column.tilemap_pos)
                rect = (tx * 16, ty * 16, (tx + max(1, column.width)) * 16, (ty + max(1, column.height)) * 16)
                if self._point_in_rect(wx, wy, rect):
                    return "column", idx, None
                if column.trigger_pos not in (0xFFFF, 0xFFFE):
                    trig_xy = self.level.tilemap_xy(column.trigger_pos)
                    trig_rect = (trig_xy[0] * 16, trig_xy[1] * 16, (trig_xy[0] + 1) * 16, (trig_xy[1] + 1) * 16)
                    if self._point_in_rect(wx, wy, trig_rect):
                        return "column", idx, None

        if not visible_overlays_only or self.overlay_bonuses.get():
            for idx, bonus in enumerate(self.level.bonuses):
                if not bonus.active:
                    continue
                tx, ty = self.level.tilemap_xy(bonus.pos)
                rect = (tx * 16, ty * 16, (tx + 1) * 16, (ty + 1) * 16)
                if self._point_in_rect(wx, wy, rect):
                    return "secret", idx, None

        if (not visible_overlays_only or self.overlay_boss.get()) and self.level.boss.active:
            boss = self.level.boss
            rect = (boss.x_pos - 24, boss.y_pos - 24, boss.x_pos + 24, boss.y_pos + 24)
            if self._point_in_rect(wx, wy, rect):
                return "boss", 0, None

        return None

    def _open_parsed_object(self, kind: str, index: int, *, center: bool = True) -> None:
        if kind == "secret" and self.level is not None and 0 <= index < len(self.level.bonuses):
            bonus = self.level.bonuses[index]
            tx, ty = self.level.tilemap_xy(bonus.pos)
            self.selected_parsed_object = ("secret", index)
            tile_num = self.level.tile_num_at(tx, ty)
            self._show_tile_properties(tile_num if tile_num is not None else bonus.initial_tile, map_xy=(tx, ty), source=f"Secret S{index}")
            self._redraw_level_overlays()
            return
        table_by_kind = {
            "monster": "Monsters",
            "item": "Items",
            "platform": "Platforms",
            "gate": "Gates",
            "column": "Columns",
            "boss": "Boss",
        }
        table_name = table_by_kind.get(kind)
        if table_name is None:
            return
        if self.table_choice.get() != table_name:
            self.table_choice.set(table_name)
            self._select_object_group_tab(table_name)
            self._refresh_tables()
        self.main_notebook.select(self.level_viewer_tab)
        self.level_side_notebook.select(self.objects_editor_tab)
        if hasattr(self, "object_tool_tabs"):
            self.object_tool_tabs.select(0)
        if center:
            self._activate_parsed_row(kind, index)
        else:
            iid = "boss:0" if kind == "boss" else f"{kind}:{index}"
            if iid in self.parsed_table_rows:
                self.tables_tree.selection_set(iid)
                self.tables_tree.focus(iid)
                self.tables_tree.see(iid)
            self.selected_parsed_object = (kind, index)
            self._refresh_parsed_detail(kind, index)
            self._redraw_level_overlays()

    def _show_tile_properties(self, tile_num: int, *, map_xy: tuple[int, int] | None = None, source: str = "Level") -> None:
        if self.level is None:
            return
        lut = self.level.lut_value(tile_num)
        attr0 = self.level.tile_attributes0[tile_num]
        attr1 = self.level.tile_attributes1[tile_num]
        attr2 = self.level.tile_attributes2[tile_num]
        attr3 = self.level.tile_attributes3[tile_num]
        self.selected_tile_context = {
            "tile_num": tile_num,
            "lut": lut,
            "map_xy": map_xy,
            "source": source,
            "attr0": attr0,
            "attr1": attr1,
            "attr2": attr2,
            "attr3": attr3,
        }
        if hasattr(self, "tile_properties_title"):
            where = f"map {map_xy}" if map_xy is not None else "tile definition"
            self.tile_properties_title.set(f"Tile 0x{tile_num:02X} from {source} — {where}, LUT 0x{lut:04X}")
        self._load_physics_lab_values(attr0, attr1, attr2, attr3)
        self._refresh_tile_properties_form(self.selected_tile_context)
        self.main_notebook.select(self.level_viewer_tab)
        self.level_side_notebook.select(self.tiles_editor_tab)
        if hasattr(self, "tiles_tool_tabs"):
            self.tiles_tool_tabs.select(2)

    def _select_level_tile(self, event) -> None:
        if self._active_level_editor_tab() == "Objects" and self.editor_tool.get() == "Select":
            return
        coords = self._level_coords_from_event(event)
        if coords is None or self.level is None:
            return
        tx, ty = coords
        tile_num = self.level.tile_num_at(tx, ty)
        lut = self.level.lut_value(tile_num or 0)
        attr0 = self.level.tile_attributes0[tile_num]
        attr1 = self.level.tile_attributes1[tile_num]
        attr2 = self.level.tile_attributes2[tile_num]
        attr3 = self.level.tile_attributes3[tile_num]
        physics = describe_tile_physics(attr0, attr1, attr3)
        self.status_text.set(
            f"Selected map ({tx}, {ty}): tile 0x{tile_num:02X}, LUT 0x{lut:04X}, "
            f"{physics}; raw attrs [0]={attr0:02X} [1]={attr1:02X} [2]={attr2:02X} [3]={attr3:02X}"
        )
        self.selected_parsed_object = None
        self._show_tile_properties(tile_num, map_xy=(tx, ty), source="Level")
        self._redraw_level_overlays()

    def _front_tile_index_from_canvas(self, event) -> int | None:
        x = int(self.front_tile_canvas.canvas.canvasx(event.x))
        y = int(self.front_tile_canvas.canvas.canvasy(event.y))
        tile_size = 32
        tx = x // tile_size
        ty = y // tile_size
        if tx < 0 or ty < 0 or tx >= 16 or ty >= 16:
            return None
        return ty * 16 + tx

    def _on_front_tile_browser_motion(self, event) -> None:
        if self.level is None:
            return
        tile_num = self._front_tile_index_from_canvas(event)
        if tile_num is None:
            return
        front_num = self.level.front_tiles_lut[tile_num]
        flagged = bool(self.level.tile_attributes2[tile_num] & 0x40)
        self.tile_info_text.set(
            f"Front source for tile {tile_num:02X}: flagged={int(flagged)} front_lut={front_num} attr2=0x{self.level.tile_attributes2[tile_num]:02X}"
        )

    def _on_front_tile_browser_click(self, event) -> None:
        self._on_front_tile_browser_motion(event)

    def _clear_tile_place_ghost(self) -> None:
        if self._tile_place_ghost_item is not None:
            self.level_canvas.canvas.delete(self._tile_place_ghost_item)
        self._tile_place_ghost_item = None
        self._tile_place_ghost_photo = None
        self._tile_place_ghost_key = None

    def _ensure_tile_place_ghost_above_map(self) -> None:
        if self._tile_place_ghost_item is None:
            return
        self.level_canvas.canvas.tag_raise(self._tile_place_ghost_item)
        if self.level_canvas.canvas.find_withtag("overlay"):
            self.level_canvas.canvas.tag_lower(self._tile_place_ghost_item, "overlay")

    def _update_tile_place_ghost(self, event) -> None:
        if (
            event is None
            or self.level is None
            or self.editor_tool.get() != "Place"
            or self._active_level_editor_tab() != "Tiles"
            or self.selected_tile_catalog_num is None
        ):
            self._clear_tile_place_ghost()
            return
        coords = self._level_coords_from_event(event)
        if coords is None:
            self._clear_tile_place_ghost()
            return
        tx, ty = coords
        scale = max(1, self.zoom.get())
        tile_num = int(self.selected_tile_catalog_num)
        key = (tx, ty, tile_num, scale)
        if self._tile_place_ghost_key == key and self._tile_place_ghost_item is not None:
            return
        tile_img = render_tile_image(
            self.level,
            self.union_tiles,
            self.palettes[self.current_level_index],
            tile_num,
            scale=scale,
            transparent_zero=True,
        ).convert("RGBA")
        alpha = tile_img.getchannel("A")
        alpha = alpha.point(lambda value: min(value, 150))
        tile_img.putalpha(alpha)
        draw = ImageDraw.Draw(tile_img, "RGBA")
        draw.rectangle((0, 0, tile_img.width - 1, tile_img.height - 1), outline=(255, 211, 77, 235), width=max(1, scale))
        photo = ImageTk.PhotoImage(tile_img)
        x = tx * 16 * scale
        y = ty * 16 * scale
        if self._tile_place_ghost_item is None:
            self._tile_place_ghost_item = self.level_canvas.canvas.create_image(
                x, y, image=photo, anchor="nw", tags=("tile_place_ghost",)
            )
        else:
            self.level_canvas.canvas.itemconfigure(self._tile_place_ghost_item, image=photo)
            self.level_canvas.canvas.coords(self._tile_place_ghost_item, x, y)
        self._tile_place_ghost_photo = photo
        self._tile_place_ghost_key = key
        self._ensure_tile_place_ghost_above_map()

    def _clear_object_place_ghost(self) -> None:
        for item in self._object_place_ghost_items:
            self.level_canvas.canvas.delete(item)
        self._object_place_ghost_items.clear()
        self._object_place_ghost_photo = None
        self._object_place_ghost_key = None

    def _ensure_object_place_ghost_above_map(self) -> None:
        for item in self._object_place_ghost_items:
            self.level_canvas.canvas.tag_raise(item)
            if self.level_canvas.canvas.find_withtag("overlay"):
                self.level_canvas.canvas.tag_lower(item, "overlay")

    def _ghosten_rgba(self, image: Image.Image, *, outline: tuple[int, int, int, int] = (255, 211, 77, 235)) -> Image.Image:
        ghost = image.convert("RGBA")
        alpha = ghost.getchannel("A")
        alpha = alpha.point(lambda value: min(value, 150))
        ghost.putalpha(alpha)
        draw = ImageDraw.Draw(ghost, "RGBA")
        draw.rectangle((0, 0, ghost.width - 1, ghost.height - 1), outline=outline, width=max(1, self.zoom.get()))
        return ghost

    def _selected_object_placement_sprite(self) -> tuple[str, int | None] | None:
        if self.level is None or self.placement_catalog_selection is None:
            return None
        kind, _source, _payload = self.placement_catalog_selection
        if kind == "item":
            draft = self._draft_object("item")
            if draft is None:
                return None
            return kind, self.sprite_resolver.item_sprite(self.level, draft.sprite_num_raw)
        if kind == "monster":
            draft = self._draft_object("monster")
            if draft is None:
                return None
            return kind, self.sprite_resolver.monster_sprite(self.level, draft.sprite_num_raw)
        if kind == "platform":
            draft = self._draft_object("platform")
            if draft is None:
                return None
            return kind, self.sprite_resolver.platform_sprite(self.level, draft.sprite_num_raw)
        if kind == "gate":
            return kind, None
        return None

    def _update_object_place_ghost(self, event) -> None:
        if (
            event is None
            or self.level is None
            or self.editor_tool.get() != "Place"
            or self._active_level_editor_tab() != "Objects"
        ):
            self._clear_object_place_ghost()
            return
        selection = self._selected_object_placement_sprite()
        world = self._world_point_from_event(event)
        tile_coords = self._level_coords_from_event(event)
        if selection is None or world is None or tile_coords is None:
            self._clear_object_place_ghost()
            return
        kind, sprite_num = selection
        scale = max(1, self.zoom.get())
        canvas = self.level_canvas.canvas

        if kind == "gate":
            tx, ty = tile_coords
            key: tuple[object, ...] = (kind, tx, ty, scale)
            if self._object_place_ghost_key == key and self._object_place_ghost_items:
                return
            self._clear_object_place_ghost()
            x0 = tx * 16 * scale
            y0 = ty * 16 * scale
            x1 = x0 + 16 * scale
            y1 = y0 + 16 * scale
            rect = canvas.create_rectangle(
                x0, y0, x1, y1,
                outline="#FFD34D",
                fill="#FFD34D",
                stipple="gray25",
                width=max(1, scale),
                tags=("object_place_ghost",),
            )
            label = canvas.create_text(
                x0 + 2 * scale,
                y0 + 2 * scale,
                text="Gate",
                fill="#FFD34D",
                anchor="nw",
                tags=("object_place_ghost",),
            )
            self._object_place_ghost_items.extend([rect, label])
            self._object_place_ghost_key = key
            self._ensure_object_place_ghost_above_map()
            return

        if kind == "monster":
            draft_monster = self._draft_object("monster")
            if draft_monster is not None and draft_monster.uses_trigger_rect:
                tx, ty = tile_coords
                _old_tx, _old_ty, tw, th = draft_monster.trigger_rect_tiles or (0, 0, 0, 0)
                x0 = tx * 16 * scale
                y0 = ty * 16 * scale
                x1 = (tx + tw + 1) * 16 * scale
                y1 = (ty + th + 1) * 16 * scale
                key = (kind, "trigger", tx, ty, tw, th, scale)
                if self._object_place_ghost_key == key and self._object_place_ghost_items:
                    return
                self._clear_object_place_ghost()
                rect = canvas.create_rectangle(
                    x0, y0, x1, y1,
                    outline="#FFD34D",
                    fill="#FFD34D",
                    stipple="gray25",
                    width=max(1, scale),
                    tags=("object_place_ghost",),
                )
                label = canvas.create_text(
                    x0 + 2 * scale,
                    y0 + 2 * scale,
                    text="Monster trigger",
                    fill="#FFD34D",
                    anchor="nw",
                    tags=("object_place_ghost",),
                )
                self._object_place_ghost_items.extend([rect, label])
                self._object_place_ghost_key = key
                self._ensure_object_place_ghost_above_map()
                return

        if sprite_num is None or not self.sprites_blob or not 0 <= sprite_num < self.sprite_tables.count:
            self._clear_object_place_ghost()
            return
        world_x, world_y = world
        x_int, y_int = int(world_x), int(world_y)
        key = (kind, sprite_num, x_int, y_int, scale)
        if self._object_place_ghost_key == key and self._object_place_ghost_items:
            return
        try:
            sprite = render_sprite_image(
                self.sprites_blob,
                self.sprite_tables,
                self.palettes[self.current_level_index],
                sprite_num,
                scale=scale,
            )
            origin_x, _origin_y = self.sprite_tables.origin(sprite_num)
            _width, height = self.sprite_tables.size(sprite_num)
        except Exception:
            self._clear_object_place_ghost()
            return
        ghost = self._ghosten_rgba(sprite)
        photo = ImageTk.PhotoImage(ghost)
        image_x = (x_int - origin_x) * scale
        image_y = (y_int - height) * scale
        self._clear_object_place_ghost()
        image_item = canvas.create_image(
            image_x,
            image_y,
            image=photo,
            anchor="nw",
            tags=("object_place_ghost",),
        )
        anchor_radius = max(3, 3 * scale)
        ax = x_int * scale
        ay = y_int * scale
        horizontal = canvas.create_line(
            ax - anchor_radius,
            ay,
            ax + anchor_radius,
            ay,
            fill="#FFD34D",
            width=max(1, scale),
            tags=("object_place_ghost",),
        )
        vertical = canvas.create_line(
            ax,
            ay - anchor_radius,
            ax,
            ay + anchor_radius,
            fill="#FFD34D",
            width=max(1, scale),
            tags=("object_place_ghost",),
        )
        self._object_place_ghost_photo = photo
        self._object_place_ghost_items.extend([image_item, horizontal, vertical])
        self._object_place_ghost_key = key
        self._ensure_object_place_ghost_above_map()

    def _on_level_motion(self, event) -> None:
        self._update_tile_place_ghost(event)
        self._update_object_place_ghost(event)
        coords = self._level_coords_from_event(event)
        if coords is None or self.level is None:
            self.tile_info_text.set("Tile: —")
            return
        tx, ty = coords
        tile_num = self.level.tile_num_at(tx, ty)
        lut = self.level.lut_value(tile_num or 0)
        self.tile_info_text.set(f"Map ({tx}, {ty}) → tile 0x{tile_num:02X}, LUT 0x{lut:04X}")

    def _tile_browser_num(self, event) -> int | None:
        x = int(self.tile_canvas.canvas.canvasx(event.x))
        y = int(self.tile_canvas.canvas.canvasy(event.y))
        tile_size = 32
        col = x // tile_size
        row = y // tile_size
        tile_num = row * 16 + col
        if 0 <= col < 16 and 0 <= row < 16 and 0 <= tile_num < 256:
            return tile_num
        return None

    def _redraw_tile_catalog_selection(self) -> None:
        if not hasattr(self, "tile_canvas"):
            return
        canvas = self.tile_canvas.canvas
        canvas.delete("tile_catalog_selection")
        self._tile_catalog_selection_outline = None
        tile_num = self.selected_tile_catalog_num
        if tile_num is None:
            return
        tile_size = 32
        col = tile_num % 16
        row = tile_num // 16
        x0 = col * tile_size
        y0 = row * tile_size
        self._tile_catalog_selection_outline = canvas.create_rectangle(
            x0 + 1,
            y0 + 1,
            x0 + tile_size - 2,
            y0 + tile_size - 2,
            outline="#FFD34D",
            width=3,
            tags=("tile_catalog_selection",),
        )
        canvas.tag_raise("tile_catalog_selection")

    def _on_tile_browser_motion(self, event) -> None:
        if self.level is None:
            return
        tile_num = self._tile_browser_num(event)
        if tile_num is None:
            return
        lut = self.level.lut_value(tile_num)
        self.tile_info_text.set(f"Tile browser: 0x{tile_num:02X}, LUT 0x{lut:04X}")

    def _on_tile_browser_click(self, event) -> None:
        if self.level is None:
            return
        tile_num = self._tile_browser_num(event)
        if tile_num is None:
            return
        lut = self.level.lut_value(tile_num)
        attr0 = self.level.tile_attributes0[tile_num]
        attr1 = self.level.tile_attributes1[tile_num]
        attr2 = self.level.tile_attributes2[tile_num]
        attr3 = self.level.tile_attributes3[tile_num]
        physics = describe_tile_physics(attr0, attr1, attr3)
        self.status_text.set(
            f"Selected tile 0x{tile_num:02X}: LUT 0x{lut:04X}, "
            f"{physics}; raw attrs [0]={attr0:02X} [1]={attr1:02X} [2]={attr2:02X} [3]={attr3:02X}"
        )
        self.selected_tile_catalog_num = tile_num
        self._redraw_tile_catalog_selection()
        if self.editor_tool.get() == "Place":
            self.status_text.set(f"Tile 0x{tile_num:02X} selected as brush. Drag with left mouse on the level to paint; right mouse erases.")
        else:
            self.status_text.set(f"Tile 0x{tile_num:02X} selected in the catalog. Switch to Place to use it as a paint brush.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prehistorik 2 level editor")
    parser.add_argument(
        "data_path",
        nargs="?",
        default="game_data",
        help="Folder containing level*.sqz, union.sqz, etc. Default: ./game_data",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = Pre2EditorApp(Path(args.data_path).resolve())
    app.mainloop()


if __name__ == "__main__":
    main()
