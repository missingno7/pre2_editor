from __future__ import annotations

import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from PIL import Image, ImageDraw, ImageTk

from pre2lib.formats import (
    LEVEL_IDS,
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
)
from pre2lib.renderer import render_level_image, render_tile_image, render_tile_atlas, render_front_tile_atlas, render_background_image
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
        self.viewport_background_source: Image.Image | None = None
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
            self.viewport_background_photo = None
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        resized = self.viewport_background_source.resize((width, height), Image.Resampling.NEAREST)
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

    def clear(self) -> None:
        self.photo = None
        self.image_item = None
        self.tiled_photos.clear()
        self.tiled_image_items.clear()
        self.viewport_background_item = None
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

    def clear(self) -> None:
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


class Pre2ViewerApp(tk.Tk):
    def __init__(self, data_path: Path):
        super().__init__()
        self.title("Prehistorik 2 — Viewer")
        self.geometry("1600x980")
        self.minsize(1160, 740)

        self.data_path = data_path
        self.project_dir = Path(__file__).resolve().parents[1]
        self.resources_dir = self.project_dir / "resources"
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
        self.tables_detail_photo: ImageTk.PhotoImage | None = None
        self.tables_detail_tile_photos: list[ImageTk.PhotoImage] = []
        self._detail_vars: list[tk.Variable] = []
        self._tile_property_vars: list[tk.Variable] = []
        self.palettes = load_palettes(self.resources_dir)
        self.current_level_index = 0

        self.difficulty = tk.StringVar(value="Expert")
        self.zoom = tk.IntVar(value=3)
        self.animation_frame_label = tk.StringVar(value="Frame 1")
        self.animate_tiles = tk.BooleanVar(value=True)
        self._animation_after_id: str | None = None
        self.grid_enabled = tk.BooleanVar(value=False)
        self.front_enabled = tk.BooleanVar(value=False)
        self.overlay_front_tiles = tk.BooleanVar(value=False)
        self.overlay_animated = tk.BooleanVar(value=False)
        self.overlay_attr1 = tk.BooleanVar(value=False)
        self.overlay_attr0 = tk.BooleanVar(value=False)
        self.overlay_attr3 = tk.BooleanVar(value=False)
        self.overlay_physics_diagram = tk.BooleanVar(value=False)

        # Object overlays are drawn from parsed level structures. A few record types
        # have different coordinate domains, handled explicitly in the renderer.
        self.overlay_player_start = tk.BooleanVar(value=True)
        self.overlay_monsters = tk.BooleanVar(value=False)
        self.overlay_items = tk.BooleanVar(value=False)
        self.overlay_platforms = tk.BooleanVar(value=False)
        self.overlay_gates = tk.BooleanVar(value=False)
        self.overlay_columns = tk.BooleanVar(value=False)
        self.overlay_bonuses = tk.BooleanVar(value=False)
        self.overlay_boss = tk.BooleanVar(value=False)

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
        self._load_static_data()
        self._load_level(0)
        self._refresh_game_files()
        self._schedule_tile_animation()

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

        ttk.Button(toolbar, text="Reload", command=self._reload).pack(side="left")

        self.main_notebook = ttk.Notebook(outer)
        self.main_notebook.pack(fill="both", expand=True)

        self.level_viewer_tab = ttk.Frame(self.main_notebook)
        self.game_file_tab = ttk.Frame(self.main_notebook, padding=8)
        self.main_notebook.add(self.level_viewer_tab, text="Level Viewer")
        self.main_notebook.add(self.game_file_tab, text="Game File Browser")

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
        self.level_canvas.canvas.bind("<B1-Motion>", self._on_level_drag)
        self.level_canvas.canvas.bind("<ButtonRelease-1>", self._on_level_release)
        self.level_canvas.canvas.bind("<Configure>", lambda _event: self._redraw_animated_tile_overlay(), add="+")

        self.side_notebook = ttk.Notebook(inspector_frame)
        self.side_notebook.pack(fill="both", expand=True)

        tile_tab = ttk.Frame(self.side_notebook)
        front_tab = ttk.Frame(self.side_notebook)
        info_tab = ttk.Frame(self.side_notebook, padding=10)
        overlays_tab = ttk.Frame(self.side_notebook, padding=10)
        tables_tab = ttk.Frame(self.side_notebook, padding=8)
        physics_lab_tab = ttk.Frame(self.side_notebook, padding=8)

        self.side_notebook.add(tile_tab, text="Tiles")
        self.side_notebook.add(front_tab, text="Front")
        self.side_notebook.add(info_tab, text="Info")
        self.side_notebook.add(overlays_tab, text="Overlays")
        self.side_notebook.add(tables_tab, text="Mechanics")
        self.side_notebook.add(physics_lab_tab, text="Tile Props")

        self.tile_canvas = ScrollableCanvas(tile_tab, bg="#202020", width=540, height=760)
        self.tile_canvas.pack(fill="both", expand=True)
        self.tile_canvas.canvas.bind("<Motion>", self._on_tile_browser_motion)
        self.tile_canvas.canvas.bind("<Button-1>", self._on_tile_browser_click)

        self.front_tile_canvas = ScrollableCanvas(front_tab, bg="#202020", width=540, height=760)
        self.front_tile_canvas.pack(fill="both", expand=True)
        self.front_tile_canvas.canvas.bind("<Motion>", self._on_front_tile_browser_motion)
        self.front_tile_canvas.canvas.bind("<Button-1>", self._on_front_tile_browser_click)

        self.info_box = tk.Text(info_tab, width=48, height=35, wrap="word", state="disabled")
        self.info_box.pack(fill="both", expand=True)

        self._build_overlays_tab(overlays_tab)
        self._build_tables_tab(tables_tab)
        self._build_physics_lab_tab(physics_lab_tab)

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
            ("Secrets", self.overlay_bonuses),
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

        list_frame = ttk.Frame(pane, padding=(0, 0, 8, 0))
        detail_frame = ttk.Frame(pane)
        pane.add(list_frame, weight=1)
        pane.add(detail_frame, weight=3)

        tables_toolbar = ttk.Frame(list_frame)
        tables_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(tables_toolbar, text="Type:").pack(side="left")
        self.table_choice = tk.StringVar(value="Monsters")
        self.table_combo = ttk.Combobox(
            tables_toolbar,
            textvariable=self.table_choice,
            state="readonly",
            width=18,
            values=["Monsters", "Items", "Platforms", "Gates", "Columns", "Secrets", "Boss"],
        )
        self.table_combo.pack(side="left", padx=(6, 0))
        self.table_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_tables())

        ttk.Label(
            list_frame,
            text="Select an object to focus it in the level viewer and inspect its properties.",
            wraplength=240,
            justify="left",
        ).pack(fill="x", pady=(0, 8))

        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill="both", expand=True)
        self.tables_tree = ttk.Treeview(tree_frame, show="headings", height=30, selectmode="browse")
        self.tables_tree.pack(side="left", fill="both", expand=True)
        self.tables_tree.bind("<<TreeviewSelect>>", self._on_parsed_table_selected)
        self.tables_tree.bind("<ButtonRelease-1>", self._on_parsed_table_click, add="+")
        self.tables_yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tables_tree.yview)
        self.tables_yscroll.pack(side="right", fill="y")
        self.tables_tree.configure(yscrollcommand=self.tables_yscroll.set)

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
            "Select an object on the left to inspect the mechanics fields that are understood so far."
        )

    def _build_physics_lab_tab(self, parent: ttk.Frame) -> None:
        intro = ttk.Label(
            parent,
            text=(
                "Tile Props is a read-only preview of the future Tile Editor. Click a tile in the Level Viewer "
                "to inspect the actual map tile. The Tiles tab stays a browser and does not change this inspector."
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

        self.tile_properties_form_container = ttk.LabelFrame(parent, text="Tile properties — future editable form", padding=6)
        self.tile_properties_form_container.pack(fill="both", expand=True)
        self.tile_properties_form = VerticalScrolledFrame(self.tile_properties_form_container)
        self.tile_properties_form.pack(fill="both", expand=True)
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
        selected = filedialog.askdirectory(initialdir=str(self.data_path), title="Choose Prehistorik 2 data folder")
        if not selected:
            return
        self.data_path = Path(selected)
        self.path_label.configure(text=str(self.data_path))
        self._load_static_data()
        self._load_level(self.current_level_index)
        self._refresh_game_files()

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
        self._load_static_data()
        self._load_level(self.current_level_index)
        self._refresh_game_files()

    def _on_level_changed(self, _event=None) -> None:
        self._load_level(self.level_combo.current())

    def _on_difficulty_changed(self, _event=None) -> None:
        self._redraw_level_overlays()
        self._refresh_info()
        self._refresh_tables()

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

    def _clear_tile_properties_form(self) -> None:
        if not hasattr(self, "tile_properties_form"):
            return
        self.tile_properties_form.clear()
        self._tile_property_vars.clear()

    def _refresh_tile_properties_form(self, context: dict[str, object] | None) -> None:
        if not hasattr(self, "tile_properties_form"):
            return
        self._clear_tile_properties_form()
        parent = self.tile_properties_form.inner
        if context is None:
            self._editor_note(parent, "Click a tile in the Level Viewer. This panel is intentionally shaped like the future Tile Editor, but it only reflects actual placed tiles from the map.")
            return

        tile_num = int(context["tile_num"])
        lut = int(context["lut"])
        map_xy = context.get("map_xy")
        attr0 = int(context["attr0"])
        attr1 = int(context["attr1"])
        attr2 = int(context["attr2"])
        attr3 = int(context["attr3"])
        underside = attr2 & 0x0F
        side_value = {0: "None", 1: "Solid", 2: "Deadly"}.get(attr0, f"Unknown {attr0}")
        top_value = {
            0: "None",
            1: "Solid",
            2: "Slightly slippery",
            3: "Slippery",
            4: "Very slippery",
            5: "Drop-through",
            6: "Deadly",
        }.get(attr1, f"Unknown {attr1}")
        bottom_value = {0: "None", 1: "Solid", 2: "Deadly"}.get(underside, f"Unknown {underside}")
        profile_value = {
            0x00: "Flat",
            0x10: "Slope down-right",
            0x20: "Slope up-right",
        }.get(attr3 & 0x30, f"Unknown profile 0x{attr3 & 0x30:02X}")

        identity = self._editor_section(parent, "Tile")
        self._add_editor_entry(identity, "Tile ID", f"0x{tile_num:02X}", 0, kind="tile")
        self._add_editor_entry(identity, "Map cell", str(map_xy), 1, kind="tile")
        self._add_editor_entry(identity, "LUT value", f"0x{lut:04X}", 2, kind="tile")

        collision = self._editor_section(parent, "Collision & surface")
        self._add_editor_combo(collision, "Side behavior", side_value, 0, kind="tile")
        self._add_editor_combo(collision, "Top behavior", top_value, 1, kind="tile")
        self._add_editor_combo(collision, "Bottom behavior", bottom_value, 2, kind="tile")
        self._add_editor_combo(collision, "Surface shape", profile_value, 3, kind="tile")
        self._add_editor_spinbox(collision, "Surface base Y", attr3 & 0x0F, 4, kind="tile", from_=0, to=15)

        animated_group = self.level.animated_tile_group_for_tile(tile_num) if self.level is not None else None
        animated_member = animated_group is not None
        animated_phase = self.level.animated_tile_phase(tile_num) if self.level is not None else None

        dynamic = self._editor_section(parent, "Dynamic / visual tile flags")
        self._add_readonly_checkbox(dynamic, "Animated tile group member", animated_member, future_editable=False, note="Derived from a 3-tile animation group base.", kind="tile")
        self._add_readonly_checkbox(dynamic, "Animation group starts on this tile", bool(attr2 & 0x80), future_editable=True, note="This is the authored attr2 0x80 base marker.", kind="tile")
        self._add_readonly_checkbox(dynamic, "Foreground / front-mask overlay", bool(attr2 & 0x40), future_editable=True, kind="tile")
        self._add_readonly_checkbox(dynamic, "Player-step decorative tile cycle", bool(attr2 & 0x20), future_editable=True, note="Runtime increments/decrements the placed tile ID while the player enters/leaves it.", kind="tile")
        self._add_readonly_checkbox(dynamic, "Fly emitter", bool(attr2 & 0x10), future_editable=True, note="Runtime spawns the small fly effect when the player touches this tile.", kind="tile")

        if animated_group is not None:
            animation = self._editor_section(parent, "Animated tile group")
            members = animated_group.members
            frame_tiles = [self.level.tile_for_animation_frame(tile_num, frame) for frame in range(3)]
            self._add_editor_entry(animation, "Group base tile", f"0x{animated_group.base_tile:02X}", 0, kind="tile")
            self._add_editor_entry(animation, "Group members", ", ".join(f"0x{member:02X}" for member in members), 1, kind="tile")
            self._add_editor_entry(animation, "Selected member phase", f"{animated_phase if animated_phase is not None else '—'} / 2", 2, kind="tile")
            self._add_editor_entry(animation, "Visual sequence for this map tile", " → ".join(f"0x{member:02X}" for member in frame_tiles), 3, kind="tile")
            self._add_editor_entry(animation, "Current viewer frame", self.animation_frame_label.get(), 4, kind="tile")

        raw = self._editor_section(parent, "Advanced / raw")
        self._add_editor_entry(raw, "attr0", f"0x{attr0:02X}", 0, kind="tile")
        self._add_editor_entry(raw, "attr1", f"0x{attr1:02X}", 1, kind="tile")
        self._add_editor_entry(raw, "attr2", f"0x{attr2:02X}", 2, kind="tile")
        self._add_editor_entry(raw, "attr3", f"0x{attr3:02X}", 3, kind="tile")

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

    def _active_object_overlay_flags(self) -> set[str]:
        flags: set[str] = set()
        if self.overlay_player_start.get():
            flags.add("player_start")
        if self.overlay_monsters.get():
            flags.add("monsters")
        if self.overlay_items.get():
            flags.add("items")
        if self.overlay_platforms.get():
            flags.add("platforms")
        if self.overlay_gates.get():
            flags.add("gates")
        if self.overlay_columns.get():
            flags.add("columns")
        if self.overlay_bonuses.get():
            flags.add("bonuses")
        if self.overlay_boss.get():
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

    def _rerender_level(self) -> None:
        if self.level is None or not self.union_tiles:
            return
        try:
            image = render_level_image(
                self.level,
                self.union_tiles,
                self.palettes[self.current_level_index],
                front_tiles=self.front_tiles,
                scale=self.zoom.get(),
                show_grid=False,
                show_front_layer=self.front_enabled.get(),
                show_flag_overlays=set(),
                show_object_overlays=set(),
                animation_frame=0,
            )
            xview = self.level_canvas.canvas.xview()
            yview = self.level_canvas.canvas.yview()
            self.level_canvas.set_pil_image(image)
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
            self.status_text.set(
                f"Level {self.level.level_id} [{self.difficulty.get()}]: "
                f"{self.level.width_tiles}×{self.level.height_tiles} tiles, "
                f"{image.width}×{image.height} px at {self.zoom.get()}×, "
                f"monsters visible {len(self._visible_monsters())}/{len(self.level.monsters)}"
            )
        except Exception as exc:
            messagebox.showerror("Render error", str(exc))

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
            for index, item in enumerate(self.level.active_items):
                spr = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
                draw_sprite(spr, item.x_pos, item.y_pos)
                marker_world(item.x_pos, item.y_pos, f"I{index}", color="#32DC78")

        if "platforms" in obj_flags:
            for index, platform in enumerate(self.level.active_platforms):
                spr = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
                draw_sprite(spr, platform.x_pos, platform.y_pos)
                marker_world(platform.x_pos, platform.y_pos, f"P{index}", color="#FF8C46")

        if "monsters" in obj_flags:
            for index, monster in enumerate(self._visible_monsters()):
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
            for index, gate in enumerate(self.level.active_gates):
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
            for index, column in enumerate(self.level.active_columns):
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
        ttk.Label(parent, text=text, wraplength=420, justify="left").pack(fill="x", anchor="w", padx=2, pady=(0, 6))

    def _editor_field_row(self, parent: ttk.LabelFrame, label: str, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)

    def _add_editor_entry(
        self,
        parent: ttk.LabelFrame,
        label: str,
        value: object,
        row: int,
        *,
        kind: str = "detail",
        width: int = 24,
    ) -> None:
        self._editor_field_row(parent, label, row)
        var = tk.StringVar(value=str(value))
        if kind == "tile":
            self._hold_tile_var(var)
        else:
            self._hold_detail_var(var)
        entry = ttk.Entry(parent, textvariable=var, width=width, state="readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=2)

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
    ) -> None:
        self._editor_field_row(parent, label, row)
        var = tk.StringVar(value=str(value))
        if kind == "tile":
            self._hold_tile_var(var)
        else:
            self._hold_detail_var(var)
        spin = ttk.Spinbox(parent, from_=from_, to=to, textvariable=var, state="readonly", width=16)
        spin.grid(row=row, column=1, sticky="ew", pady=2)
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
    ) -> None:
        self._editor_field_row(parent, label, row)
        var = tk.StringVar(value=str(value))
        if kind == "tile":
            self._hold_tile_var(var)
        else:
            self._hold_detail_var(var)
        combo = ttk.Combobox(parent, textvariable=var, values=[str(value)], state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=2)
        combo.bind("<<ComboboxSelected>>", lambda _event, v=var, original=str(value): v.set(original))

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
        if future_editable:
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

    def _build_parsed_detail_controls(self, kind: str, index: int) -> None:
        self._clear_parsed_detail_controls()
        if self.level is None:
            return
        parent = self.tables_detail_form.inner

        if kind == "monster" and 0 <= index < len(self.level.monsters):
            monster = self.level.monsters[index]
            sprite_view = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
            visual_name = monster_visual_name(sprite_view)
            title = monster_display_name(sprite_view, monster.behavior_name)
            self._editor_note(parent, f"{title}. This is already laid out like the future Enemy Editor; the current milestone keeps it read-only.")

            general = self._editor_section(parent, "Enemy")
            self._add_editor_combo(general, "Visual", visual_name, 0)
            self._add_editor_combo(general, "Behavior", monster.behavior_name, 1)
            self._add_editor_spinbox(general, "Energy", monster.energy, 2, from_=0, to=255)
            self._add_editor_spinbox(general, "Score value", monster.score, 3, from_=0, to=65535)
            self._add_editor_spinbox(general, "Respawn / activation ticks", monster.respawn_ticks, 4, from_=0, to=255)

            flags = self._editor_section(parent, "Flags")
            self._add_readonly_checkbox(flags, "Expert-only enemy", monster.expert_only, future_editable=True, note="Stored in the level type byte.")
            if monster.movement_type == 9:
                self._add_readonly_checkbox(flags, "Patrol follows terrain / ground physics", bool(monster.flags & 0x08), future_editable=True, note="Confirmed authored T9 flag.")
            self._add_readonly_checkbox(flags, "Uses trigger rectangle instead of fixed spawn point", monster.uses_trigger_rect, future_editable=False, note="Derived from the selected behavior.")
            self._add_readonly_checkbox(flags, "Uses fixed anchor / placement point", not monster.uses_trigger_rect, future_editable=False, note="Derived from the selected behavior.")

            placement = self._editor_section(parent, "Placement")
            if monster.uses_trigger_rect and monster.trigger_rect_tiles is not None:
                tx, ty, tw, th = monster.trigger_rect_tiles
                self._add_editor_spinbox(placement, "Trigger X tile", tx, 0, from_=0, to=255)
                self._add_editor_spinbox(placement, "Trigger Y tile", ty, 1, from_=0, to=255)
                self._add_editor_spinbox(placement, "Trigger width", tw + 1, 2, from_=1, to=256)
                self._add_editor_spinbox(placement, "Trigger height", th + 1, 3, from_=1, to=256)
            else:
                self._add_editor_spinbox(placement, "Anchor X", monster.x_pos, 0, from_=-32768, to=32767)
                self._add_editor_spinbox(placement, "Anchor Y", monster.y_pos, 1, from_=-32768, to=32767)

            behavior = self._editor_section(parent, "Behavior parameters")
            behavior_row = 0
            for key, value in monster.extra.items():
                if key.startswith("initial_runtime_") or key == "record_tail_bytes":
                    continue
                if isinstance(value, (int, float)):
                    self._add_editor_spinbox(behavior, self._monster_extra_label(key), value, behavior_row)
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
            item = self.level.items[index]
            sprite_view = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
            item_name = item_visual_name(sprite_view)
            self._editor_note(parent, "Item placement is shown in the same shape the future item editor can use.")
            general = self._editor_section(parent, "Item")
            self._add_editor_combo(general, "Item type", item_name, 0)
            self._add_readonly_checkbox(general, "Item slot is active", item.active, future_editable=True, note="Stored in the level item table.")
            placement = self._editor_section(parent, "Placement")
            self._add_editor_spinbox(placement, "X", item.x_pos, 0)
            self._add_editor_spinbox(placement, "Y", item.y_pos, 1)
            self._add_editor_spinbox(placement, "Y delta / tuning", item.y_delta, 2)
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Level record", f"I{index}", 0)
            self._add_editor_entry(raw, "Raw sprite number", item.sprite_num_raw, 1)
            self._add_editor_entry(raw, "Resolved sprite frame", sprite_view, 2)
            return

        if kind == "platform" and 0 <= index < len(self.level.platforms):
            platform = self.level.platforms[index]
            sprite_view = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
            visual_name = platform_visual_name(sprite_view)
            behavior_name = platform_behavior_name(platform.platform_type)
            self._editor_note(parent, "Platform behavior is already normalized to a friendly dropdown-style field.")
            general = self._editor_section(parent, "Platform")
            self._add_editor_combo(general, "Visual", visual_name, 0)
            self._add_editor_combo(general, "Behavior", behavior_name, 1)
            self._add_readonly_checkbox(general, "Platform slot is active", platform.active, future_editable=True)
            placement = self._editor_section(parent, "Placement")
            self._add_editor_spinbox(placement, "X", platform.x_pos, 0)
            self._add_editor_spinbox(placement, "Y", platform.y_pos, 1)
            params = self._editor_section(parent, "Behavior parameters")
            row = 0
            for key, value in platform.extra.items():
                label = self._platform_extra_label(platform, key)
                if isinstance(value, (int, float)):
                    self._add_editor_spinbox(params, label, value, row)
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
            gate = self.level.gates[index]
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
            source = self._editor_section(parent, "Source / entry")
            self._add_editor_spinbox(source, "Tile X", enter_xy[0], 0, from_=0, to=255)
            self._add_editor_spinbox(source, "Tile Y", enter_xy[1], 1, from_=0, to=255)
            dest = self._editor_section(parent, "Destination")
            self._add_editor_spinbox(dest, "Tile X", dst_xy[0], 0, from_=0, to=255)
            self._add_editor_spinbox(dest, "Tile Y", dst_xy[1], 1, from_=0, to=255)
            options = self._editor_section(parent, "Options")
            self._add_editor_spinbox(options, "Scroll flag", gate.scroll_flag, 0, from_=0, to=255)
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "enter_pos", f"0x{gate.enter_pos:04X}", 0)
            self._add_editor_entry(raw, "tilemap_pos", f"0x{gate.tilemap_pos:04X}", 1)
            self._add_editor_entry(raw, "dst_pos", f"0x{gate.dst_pos:04X}", 2)
            return

        if kind == "column" and 0 <= index < len(self.level.columns):
            column = self.level.columns[index]
            map_xy = self.level.tilemap_xy(column.tilemap_pos)
            trigger_xy = self.level.tilemap_xy(column.trigger_pos)
            general = self._editor_section(parent, "Shifting column")
            self._add_editor_spinbox(general, "Origin tile X", map_xy[0], 0, from_=0, to=255)
            self._add_editor_spinbox(general, "Origin tile Y", map_xy[1], 1, from_=0, to=255)
            self._add_editor_spinbox(general, "Width", column.width, 2, from_=0, to=255)
            self._add_editor_spinbox(general, "Height", column.height, 3, from_=0, to=255)
            trigger = self._editor_section(parent, "Trigger / target")
            self._add_editor_spinbox(trigger, "Trigger tile X", trigger_xy[0], 0, from_=0, to=255)
            self._add_editor_spinbox(trigger, "Trigger tile Y", trigger_xy[1], 1, from_=0, to=255)
            self._add_editor_spinbox(trigger, "Target Y", column.y_target, 2)
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Tiles buffer offset", f"0x{column.tiles_offset_buf:04X}", 0)
            self._add_editor_entry(raw, "Runtime state / tuning byte", column.unk9, 1)
            self._add_editor_entry(raw, "raw unk9", column.unk9, 2)
            return

        if kind == "secret" and 0 <= index < len(self.level.bonuses):
            bonus = self.level.bonuses[index]
            pos_xy = self.level.tilemap_xy(bonus.pos)
            secret_names = {
                "small_random_bonus": "Small random bonus secret",
                "tile_reveal": "Tile reveal / appearing structure secret",
                "big_random_bonus": "Big random bonus secret",
            }
            general = self._editor_section(parent, "Secret")
            self._add_editor_combo(general, "Secret type", secret_names.get(bonus.mode, bonus.mode), 0)
            self._add_editor_spinbox(general, "Estimated hit count", bonus.hit_count_estimate, 1, from_=0, to=255)
            location = self._editor_section(parent, "Location / revealed tile")
            self._add_editor_spinbox(location, "Tile X", pos_xy[0], 0, from_=0, to=255)
            self._add_editor_spinbox(location, "Tile Y", pos_xy[1], 1, from_=0, to=255)
            self._add_editor_entry(location, "Initial tile", f"0x{bonus.tile_num0:02X}", 2)
            self._add_editor_entry(location, "Revealed tile", f"0x{bonus.tile_num1:02X}", 3)
            raw = self._editor_section(parent, "Advanced / raw")
            self._add_editor_entry(raw, "Secret slot", f"S{index}", 0)
            self._add_editor_entry(raw, "Count byte", f"0x{bonus.count:02X}", 1)
            self._add_editor_entry(raw, "Tilemap pos", f"0x{bonus.pos:04X}", 2)
            return

        if kind == "boss":
            boss = self.level.boss
            general = self._editor_section(parent, "Boss controller")
            self._add_readonly_checkbox(general, "Boss controller active", boss.active, future_editable=True)
            self._add_editor_spinbox(general, "Anchor X", boss.x_pos, 0)
            self._add_editor_spinbox(general, "Anchor Y", boss.y_pos, 1)
            self._add_editor_spinbox(general, "Allowed X min", boss.x_min, 2)
            self._add_editor_spinbox(general, "Allowed X max", boss.x_max, 3)
            self._add_editor_spinbox(general, "Speed", boss.speed, 4)
            self._add_editor_spinbox(general, "Energy", boss.energy, 5)
            self._add_editor_spinbox(general, "State", boss.state, 6)
            return

        self._editor_note(parent, "Select an object on the left to inspect its future editor form.")

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
            return float(item.x_pos), float(item.y_pos)
        if kind == "platform":
            platform = self.level.platforms[index]
            return float(platform.x_pos), float(platform.y_pos)
        if kind == "gate":
            gate = self.level.gates[index]
            focus_side = self.gate_focus_side.get(index, "source")
            focus_pos = gate.dst_pos if focus_side == "destination" else gate.enter_pos
            tx, ty = self.level.tilemap_xy(focus_pos)
            return tx * 16 + 8.0, ty * 16 + 8.0
        if kind == "column":
            column = self.level.columns[index]
            tx, ty = self.level.tilemap_xy(column.tilemap_pos)
            return (tx + max(1, column.width) / 2) * 16, (ty + max(1, column.height) / 2) * 16
        if kind == "secret":
            bonus = self.level.bonuses[index]
            tx, ty = self.level.tilemap_xy(bonus.pos)
            return tx * 16 + 8.0, ty * 16 + 8.0
        if kind == "boss":
            boss = self.level.boss
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

    def _on_parsed_table_selected(self, _event=None) -> None:
        selection = self.tables_tree.selection()
        if not selection:
            return
        iid = selection[0]
        kind_index = self.parsed_table_rows.get(iid)
        if kind_index is None:
            return
        kind, index = kind_index
        self._focus_parsed_object(kind, index)
        self._refresh_parsed_detail(kind, index)

    def _on_parsed_table_click(self, event) -> None:
        """Mouse click behavior for read-only object lists.

        Gates deliberately cycle between source and destination on repeated clicks,
        so the table behaves like a compact teleporter navigator.
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
        self.after_idle(lambda: self._activate_parsed_row("gate", index))

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
            monster = self.level.monsters[index]
            sprite_view = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
            visual_name = monster_visual_name(sprite_view)
            title = monster_display_name(sprite_view, monster.behavior_name)
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
            item = self.level.items[index]
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
            platform = self.level.platforms[index]
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
            gate = self.level.gates[index]
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
            column = self.level.columns[index]
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
            bonus = self.level.bonuses[index]
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
            boss = self.level.boss
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
        self._set_parsed_detail_text("Select an object on the left to inspect it and focus it in the level viewer.")

        if selected == "Monsters":
            cols = ["Enemy", "Behavior", "Difficulty", "Location"]
            self._configure_table_tree(cols)
            for idx, monster in enumerate(self.level.monsters):
                sprite_view = self.sprite_resolver.monster_sprite(self.level, monster.sprite_num_raw)
                enemy_name = monster_visual_name(sprite_view)
                self._insert_parsed_row(
                    f"monster:{idx}",
                    "monster",
                    idx,
                    f"M{idx}: {enemy_name}",
                )

        elif selected == "Items":
            cols = ["Item", "Location"]
            self._configure_table_tree(cols)
            for idx, item in enumerate(self.level.items):
                if not item.active:
                    continue
                sprite_view = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
                self._insert_parsed_row(
                    f"item:{idx}",
                    "item",
                    idx,
                    f"I{idx}: {item_visual_name(sprite_view)}",
                )

        elif selected == "Platforms":
            cols = ["Platform", "Behavior", "Location"]
            self._configure_table_tree(cols)
            for idx, platform in enumerate(self.level.platforms):
                if not platform.active:
                    continue
                sprite_view = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
                self._insert_parsed_row(
                    f"platform:{idx}",
                    "platform",
                    idx,
                    f"P{idx}: {platform_visual_name(sprite_view)}",
                )

        elif selected == "Gates":
            cols = ["Gate", "Entry", "Destination"]
            self._configure_table_tree(cols)
            for idx, gate in enumerate(self.level.gates):
                if not gate.active:
                    continue
                self._insert_parsed_row(
                    f"gate:{idx}",
                    "gate",
                    idx,
                    f"G{idx}: Gate",
                )

        elif selected == "Columns":
            cols = ["Column", "Location", "Size", "Trigger"]
            self._configure_table_tree(cols)
            for idx, column in enumerate(self.level.columns):
                if not column.active:
                    continue
                self._insert_parsed_row(
                    f"column:{idx}",
                    "column",
                    idx,
                    f"C{idx}: Shifting column",
                )

        elif selected == "Secrets":
            cols = ["Secret", "Type", "Location", "Hits~"]
            self._configure_table_tree(cols)
            names = {
                "small_random_bonus": "Small random bonus",
                "tile_reveal": "Tile reveal / appearing structure",
                "big_random_bonus": "Big random bonus",
            }
            for idx, bonus in enumerate(self.level.bonuses):
                if not bonus.active:
                    continue
                self._insert_parsed_row(
                    f"secret:{idx}",
                    "secret",
                    idx,
                    f"S{idx}: {names.get(bonus.mode, bonus.mode)}",
                )

        elif selected == "Boss":
            cols = ["Boss", "Location", "Range", "Energy"]
            self._configure_table_tree(cols)
            boss = self.level.boss
            if boss.active:
                self._insert_parsed_row(
                    "boss:0",
                    "boss",
                    0,
                    "Boss controller",
                )

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

    def _on_level_press(self, event) -> None:
        self._level_pan_state = {"x": event.x, "y": event.y, "dragged": False}
        self.level_canvas.canvas.scan_mark(event.x, event.y)

    def _on_level_drag(self, event) -> None:
        if self._level_pan_state is None:
            return
        dx = abs(event.x - int(self._level_pan_state["x"]))
        dy = abs(event.y - int(self._level_pan_state["y"]))
        if dx > 3 or dy > 3:
            self._level_pan_state["dragged"] = True
        self.level_canvas.canvas.scan_dragto(event.x, event.y, gain=1)
        self.level_canvas.position_viewport_background()
        self._redraw_animated_tile_overlay()
        self._on_level_motion(event)

    def _on_level_release(self, event) -> None:
        state = self._level_pan_state
        self._level_pan_state = None
        if state is not None and not bool(state["dragged"]):
            self._select_level_at_event(event)

    def _select_level_at_event(self, event) -> None:
        object_hit = self._object_hit_from_event(event)
        if object_hit is not None:
            kind, index, gate_side = object_hit
            if kind == "gate" and gate_side is not None:
                current = self.gate_focus_side.get(index, "source")
                if self.selected_parsed_object == ("gate", index) and current == gate_side:
                    self.gate_focus_side[index] = "destination" if current == "source" else "source"
                else:
                    self.gate_focus_side[index] = gate_side
            self._open_parsed_object(kind, index)
            return
        self._select_level_tile(event)

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

    def _object_hit_from_event(self, event) -> tuple[str, int, str | None] | None:
        """Return a parsed object under the click, but only from currently visible overlay layers."""
        if self.level is None:
            return None
        point = self._world_point_from_event(event)
        if point is None:
            return None
        wx, wy = point

        if self.overlay_items.get():
            for idx, item in enumerate(self.level.items):
                if not item.active:
                    continue
                spr = self.sprite_resolver.item_sprite(self.level, item.sprite_num_raw)
                bbox = self._sprite_world_bbox(spr, item.x_pos, item.y_pos)
                if bbox is not None and self._point_in_rect(wx, wy, bbox, pad=3.0):
                    return "item", idx, None

        if self.overlay_monsters.get():
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

        if self.overlay_platforms.get():
            for idx, platform in enumerate(self.level.platforms):
                if not platform.active:
                    continue
                spr = self.sprite_resolver.platform_sprite(self.level, platform.sprite_num_raw)
                bbox = self._sprite_world_bbox(spr, platform.x_pos, platform.y_pos)
                if bbox is not None and self._point_in_rect(wx, wy, bbox, pad=4.0):
                    return "platform", idx, None

        if self.overlay_gates.get():
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

        if self.overlay_columns.get():
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

        if self.overlay_bonuses.get():
            for idx, bonus in enumerate(self.level.bonuses):
                if not bonus.active:
                    continue
                tx, ty = self.level.tilemap_xy(bonus.pos)
                rect = (tx * 16, ty * 16, (tx + 1) * 16, (ty + 1) * 16)
                if self._point_in_rect(wx, wy, rect):
                    return "secret", idx, None

        if self.overlay_boss.get() and self.level.boss.active:
            boss = self.level.boss
            rect = (boss.x_pos - 24, boss.y_pos - 24, boss.x_pos + 24, boss.y_pos + 24)
            if self._point_in_rect(wx, wy, rect):
                return "boss", 0, None

        return None

    def _open_parsed_object(self, kind: str, index: int) -> None:
        table_by_kind = {
            "monster": "Monsters",
            "item": "Items",
            "platform": "Platforms",
            "gate": "Gates",
            "column": "Columns",
            "secret": "Secrets",
            "boss": "Boss",
        }
        table_name = table_by_kind.get(kind)
        if table_name is None:
            return
        if self.table_choice.get() != table_name:
            self.table_choice.set(table_name)
            self._refresh_tables()
        self.side_notebook.select(4)
        self._activate_parsed_row(kind, index)

    def _show_tile_properties(self, tile_num: int, *, map_xy: tuple[int, int] | None = None, source: str = "Level Viewer") -> None:
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
        self.side_notebook.select(5)

    def _select_level_tile(self, event) -> None:
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
        self._show_tile_properties(tile_num, map_xy=(tx, ty), source="Level Viewer")

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

    def _on_level_motion(self, event) -> None:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prehistorik 2 tile browser and level viewer")
    parser.add_argument(
        "data_path",
        nargs="?",
        default="game_data",
        help="Folder containing level*.sqz, union.sqz, etc. Default: ./game_data",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = Pre2ViewerApp(Path(args.data_path).resolve())
    app.mainloop()


if __name__ == "__main__":
    main()
