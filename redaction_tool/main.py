"""
Secure Document Redaction Tool — Desktop GUI (tkinter)

Supported file types: PDF, DOCX, XLSX, JPG/PNG/TIFF/BMP
Features:
  • Auto-detect PAN, TAN, GSTIN, CIN, Aadhaar, Phone, Email
  • Custom keyword list with bulk import
  • Saveable/loadable redaction profiles
  • Per-file progress + Audit log with export
  • Output: same format as input, saved to _redacted/ subfolder

Run:  python main.py
"""

import sys
import os
import re
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.patterns import PATTERN_GROUPS, get_active_patterns
from core.profile_manager import ProfileManager
from core.engine import RedactionEngine, SUPPORTED_EXTENSIONS

# ── Colour palette ────────────────────────────────────────────────────────────
C_HDR_BG   = "#1a3a5c"
C_HDR_FG   = "#ffffff"
C_HDR_SUB  = "#a8c8e8"
C_BTN      = "#2d6a9f"
C_BTN_DARK = "#1a5480"
C_BAR_BG   = "#e8eaed"
C_WHITE    = "#ffffff"
C_LIGHT    = "#f7f9fc"
C_BORDER   = "#d0d5dd"
C_MUTED    = "#6b7280"
C_SUCCESS  = "#15803d"
C_WARN     = "#b45309"
C_ERROR    = "#b91c1c"

SUMMARY_COLORS = {
    "files":   "#2d6a9f",
    "total":   "#15803d",
    "pan_tan": "#7c3aed",
    "gst_cin": "#c2410c",
    "aadhaar": "#0369a1",
    "custom":  "#374151",
}


# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

class RedactApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Secure Document Redaction Tool v1.0")
        self.geometry("1180x800")
        self.minsize(960, 660)
        self.configure(bg=C_BAR_BG)

        # ── Application state ─────────────────────────────────────────────────
        self.file_paths: list[str] = []
        self.audit_entries: list[dict] = []
        self.custom_keywords: list[str] = []

        # ── tk variables ──────────────────────────────────────────────────────
        self.replacement_var = tk.StringVar(value="[REDACTED]")
        self.case_var        = tk.BooleanVar(value=False)
        self.output_dir_var  = tk.StringVar(value="")
        self.pattern_vars    = {k: tk.BooleanVar(value=True) for k in PATTERN_GROUPS}

        # ── Core objects ──────────────────────────────────────────────────────
        self.profile_mgr = ProfileManager()
        self.engine      = RedactionEngine()

        self._setup_style()
        self._build_ui()

    # ── Style ──────────────────────────────────────────────────────────────────

    def _setup_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")

        s.configure("TNotebook",        background=C_BAR_BG, borderwidth=0)
        s.configure("TNotebook.Tab",    padding=[16, 8], font=("Segoe UI", 10),
                    background="#d1d9e6", foreground="#374151")
        s.map("TNotebook.Tab",
              background=[("selected", C_WHITE)],
              foreground=[("selected", C_HDR_BG)])

        s.configure("Treeview",         font=("Segoe UI", 9), rowheight=24,
                    fieldbackground=C_WHITE, background=C_WHITE)
        s.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"),
                    background="#e8eaed", foreground="#1f2937")
        s.map("Treeview", background=[("selected", "#bfdbfe")])

        s.configure("Action.TButton",   font=("Segoe UI", 11, "bold"), padding=[22, 9])
        s.configure("Small.TButton",    font=("Segoe UI", 9),          padding=[10, 4])
        s.configure("TCheckbutton",     background=C_WHITE, font=("Segoe UI", 9))
        s.configure("TLabelframe",      background=C_WHITE)
        s.configure("TLabelframe.Label",background=C_WHITE, font=("Segoe UI", 9, "bold"),
                    foreground=C_HDR_BG)

    # ── Top-level UI ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header bar
        hdr = tk.Frame(self, bg=C_HDR_BG, height=58)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🔒  Secure Document Redaction Tool",
                 bg=C_HDR_BG, fg=C_HDR_FG,
                 font=("Segoe UI", 15, "bold")).pack(side="left", padx=18, pady=10)
        tk.Label(hdr, text="Protect • Redact • Share Safely",
                 bg=C_HDR_BG, fg=C_HDR_SUB,
                 font=("Segoe UI", 9)).pack(side="right", padx=18)

        # Notebook
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        for attr, label in [
            ("tab_files",    "  📂 Files  "),
            ("tab_rules",    "  ⚙  Rules  "),
            ("tab_profiles", "  💾 Profiles  "),
            ("tab_audit",    "  📊 Audit Log  "),
        ]:
            f = tk.Frame(self.nb, bg=C_WHITE)
            setattr(self, attr, f)
            self.nb.add(f, text=label)

        self._build_files_tab()
        self._build_rules_tab()
        self._build_profiles_tab()
        self._build_audit_tab()
        self._build_bottom_bar()

    # ── Files Tab ──────────────────────────────────────────────────────────────

    def _build_files_tab(self):
        p = self.tab_files
        p.configure(padx=12, pady=12)

        # Button row
        btn_row = tk.Frame(p, bg=C_WHITE)
        btn_row.pack(fill="x", pady=(0, 8))

        ttk.Button(btn_row, text="➕ Add Files",   style="Small.TButton",
                   command=self._add_files).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="📁 Add Folder",  style="Small.TButton",
                   command=self._add_folder).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="✖ Remove",       style="Small.TButton",
                   command=self._remove_selected).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Clear All",      style="Small.TButton",
                   command=self._clear_files).pack(side="left")

        self.file_count_var = tk.StringVar(value="No files added")
        tk.Label(btn_row, textvariable=self.file_count_var, bg=C_WHITE,
                 fg=C_MUTED, font=("Segoe UI", 9)).pack(side="right")

        # File list treeview
        tv_frame = tk.Frame(p, bg=C_WHITE, relief="flat",
                            highlightbackground=C_BORDER, highlightthickness=1)
        tv_frame.pack(fill="both", expand=True)

        cols = ("#", "Name", "Type", "Size", "Folder")
        self.file_tree = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                      selectmode="extended")
        for col, width, anchor in [
            ("#", 42, "center"), ("Name", 290, "w"), ("Type", 75, "center"),
            ("Size", 90, "e"),   ("Folder", 400, "w"),
        ]:
            self.file_tree.heading(col, text=col)
            self.file_tree.column(col, width=width, anchor=anchor, stretch=(col == "Folder"))

        vsb = ttk.Scrollbar(tv_frame, orient="vertical",   command=self.file_tree.yview)
        hsb = ttk.Scrollbar(tv_frame, orient="horizontal", command=self.file_tree.xview)
        self.file_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tv_frame.grid_rowconfigure(0, weight=1)
        tv_frame.grid_columnconfigure(0, weight=1)

        # Output folder
        out_lf = ttk.LabelFrame(p, text=" Output Folder ", padding=(10, 6))
        out_lf.pack(fill="x", pady=(10, 0))
        ttk.Entry(out_lf, textvariable=self.output_dir_var, width=72).pack(side="left", padx=(0, 6))
        ttk.Button(out_lf, text="Browse…", style="Small.TButton",
                   command=self._browse_output).pack(side="left")
        tk.Label(out_lf,
                 text="  Leave blank → files saved to  _redacted/  subfolder beside each source file",
                 bg=C_WHITE, fg=C_MUTED, font=("Segoe UI", 8)).pack(side="left")

    # ── Rules Tab ──────────────────────────────────────────────────────────────

    def _build_rules_tab(self):
        p = self.tab_rules
        p.configure(padx=12, pady=12)

        left  = tk.Frame(p, bg=C_WHITE)
        right = tk.Frame(p, bg=C_WHITE)
        left.pack(side="left", fill="y", padx=(0, 14))
        right.pack(side="left", fill="both", expand=True)

        # ── Auto-detect patterns ──────────────────────────────────────────────
        pat_lf = ttk.LabelFrame(left, text=" Auto-Detect Patterns ", padding=(14, 10))
        pat_lf.pack(fill="x", pady=(0, 12))

        for key, grp in PATTERN_GROUPS.items():
            cb = ttk.Checkbutton(pat_lf, text=grp["label"], variable=self.pattern_vars[key])
            cb.pack(anchor="w", pady=4)

        # ── Redaction settings ────────────────────────────────────────────────
        cfg_lf = ttk.LabelFrame(left, text=" Redaction Settings ", padding=(14, 10))
        cfg_lf.pack(fill="x", pady=(0, 12))

        tk.Label(cfg_lf, text="Replacement Text:", bg=C_WHITE,
                 font=("Segoe UI", 9)).pack(anchor="w")
        ttk.Entry(cfg_lf, textvariable=self.replacement_var, width=26).pack(
            anchor="w", pady=(2, 10))
        ttk.Checkbutton(cfg_lf, text="Case Sensitive Matching",
                        variable=self.case_var).pack(anchor="w")
        tk.Label(cfg_lf, text="\nPreview:", bg=C_WHITE,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.preview_lbl = tk.Label(
            cfg_lf, text="ABCDE1234F  →  [REDACTED]",
            bg="#fef9c3", fg="#854d0e",
            font=("Consolas", 9), relief="flat", padx=8, pady=5,
        )
        self.preview_lbl.pack(anchor="w")
        self.replacement_var.trace_add("write", self._update_preview)

        # ── Custom keywords ───────────────────────────────────────────────────
        kw_lf = ttk.LabelFrame(right, text=" Custom Keywords & Phrases ", padding=(14, 10))
        kw_lf.pack(fill="both", expand=True)

        # Single entry
        row1 = tk.Frame(kw_lf, bg=C_WHITE)
        row1.pack(fill="x", pady=(0, 6))
        self.kw_entry = ttk.Entry(row1, font=("Segoe UI", 10), width=42)
        self.kw_entry.pack(side="left", padx=(0, 6))
        self.kw_entry.bind("<Return>", lambda _: self._add_keyword())
        ttk.Button(row1, text="Add",             style="Small.TButton",
                   command=self._add_keyword).pack(side="left", padx=(0, 4))
        ttk.Button(row1, text="Remove Selected", style="Small.TButton",
                   command=self._remove_keyword).pack(side="left")

        # Bulk import
        tk.Label(kw_lf, text="Bulk import — comma or newline separated:",
                 bg=C_WHITE, fg=C_MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))
        row2 = tk.Frame(kw_lf, bg=C_WHITE)
        row2.pack(fill="x", pady=(0, 8))
        self.bulk_text = tk.Text(row2, height=3, font=("Segoe UI", 9),
                                 relief="solid", bd=1)
        self.bulk_text.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row2, text="Import", style="Small.TButton",
                   command=self._bulk_import).pack(side="left", anchor="n")

        # Keyword listbox
        lb_frame = tk.Frame(kw_lf, bg=C_WHITE,
                            highlightbackground=C_BORDER, highlightthickness=1)
        lb_frame.pack(fill="both", expand=True)
        self.kw_listbox = tk.Listbox(lb_frame, font=("Segoe UI", 9),
                                     selectmode="extended", relief="flat",
                                     selectbackground="#bfdbfe",
                                     activestyle="none")
        kw_vsb = ttk.Scrollbar(lb_frame, orient="vertical",
                                command=self.kw_listbox.yview)
        self.kw_listbox.configure(yscrollcommand=kw_vsb.set)
        self.kw_listbox.pack(side="left", fill="both", expand=True)
        kw_vsb.pack(side="right", fill="y")

        self.kw_count_var = tk.StringVar(value="0 keywords")
        tk.Label(kw_lf, textvariable=self.kw_count_var, bg=C_WHITE,
                 fg=C_MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

    # ── Profiles Tab ───────────────────────────────────────────────────────────

    def _build_profiles_tab(self):
        p = self.tab_profiles
        p.configure(padx=12, pady=12)

        left  = tk.Frame(p, bg=C_WHITE)
        right = ttk.LabelFrame(p, text=" Profile Actions ", padding=(16, 14))
        left.pack(side="left",  fill="both", expand=True, padx=(0, 12))
        right.pack(side="right", fill="y")

        tk.Label(left, text="Saved Profiles", bg=C_WHITE,
                 font=("Segoe UI", 11, "bold"), fg=C_HDR_BG).pack(anchor="w", pady=(0, 6))

        pf_frame = tk.Frame(left, bg=C_WHITE,
                            highlightbackground=C_BORDER, highlightthickness=1)
        pf_frame.pack(fill="both", expand=True)

        cols = ("Name", "Keywords", "Created")
        self.prof_tree = ttk.Treeview(pf_frame, columns=cols, show="headings", height=18)
        for col, w, anc in [("Name", 220, "w"), ("Keywords", 80, "center"), ("Created", 160, "center")]:
            self.prof_tree.heading(col, text=col)
            self.prof_tree.column(col, width=w, anchor=anc)
        pf_vsb = ttk.Scrollbar(pf_frame, orient="vertical", command=self.prof_tree.yview)
        self.prof_tree.configure(yscrollcommand=pf_vsb.set)
        self.prof_tree.pack(side="left", fill="both", expand=True)
        pf_vsb.pack(side="right", fill="y")

        # Actions panel (right)
        tk.Label(right, text="Profile Name:", bg=C_WHITE,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.prof_name_var = tk.StringVar()
        ttk.Entry(right, textvariable=self.prof_name_var, width=30).pack(
            anchor="w", pady=(2, 14))

        ttk.Button(right, text="💾  Save Current Rules",  style="Small.TButton",
                   command=self._save_profile).pack(fill="x", pady=3)
        ttk.Button(right, text="📂  Load Selected",       style="Small.TButton",
                   command=self._load_profile).pack(fill="x", pady=3)
        ttk.Button(right, text="🗑   Delete Selected",    style="Small.TButton",
                   command=self._delete_profile).pack(fill="x", pady=3)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=14)

        tk.Label(right, text="What a profile stores:", bg=C_WHITE,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(right,
                 text="  • Enabled auto-detect patterns\n"
                      "  • All custom keywords\n"
                      "  • Replacement text\n"
                      "  • Case sensitivity setting",
                 bg=C_WHITE, fg=C_MUTED, font=("Segoe UI", 9),
                 justify="left").pack(anchor="w", pady=(4, 0))

        self._refresh_profile_list()

    # ── Audit Log Tab ──────────────────────────────────────────────────────────

    def _build_audit_tab(self):
        p = self.tab_audit
        p.configure(padx=12, pady=12)

        # Summary KPI boxes
        kpi_row = tk.Frame(p, bg=C_WHITE)
        kpi_row.pack(fill="x", pady=(0, 10))

        self._kpi_vars: dict[str, tk.StringVar] = {}
        for key, label, color in [
            ("files",   "Files Processed", SUMMARY_COLORS["files"]),
            ("total",   "Total Redactions", SUMMARY_COLORS["total"]),
            ("pan_tan", "PAN / TAN",        SUMMARY_COLORS["pan_tan"]),
            ("gst_cin", "GST / CIN",        SUMMARY_COLORS["gst_cin"]),
            ("aadhaar", "Aadhaar / Phone",  SUMMARY_COLORS["aadhaar"]),
            ("custom",  "Custom Keywords",  SUMMARY_COLORS["custom"]),
        ]:
            box = tk.Frame(kpi_row, bg=color, padx=18, pady=10)
            box.pack(side="left", padx=(0, 6))
            v = tk.StringVar(value="0")
            tk.Label(box, textvariable=v, bg=color, fg="white",
                     font=("Segoe UI", 20, "bold")).pack()
            tk.Label(box, text=label, bg=color, fg="#c7d9ed",
                     font=("Segoe UI", 8)).pack()
            self._kpi_vars[key] = v

        # Audit treeview
        at_frame = tk.Frame(p, bg=C_WHITE,
                            highlightbackground=C_BORDER, highlightthickness=1)
        at_frame.pack(fill="both", expand=True)

        cols = ("File", "Location", "Category", "Term Found", "Count")
        self.audit_tree = ttk.Treeview(at_frame, columns=cols, show="headings")
        for col, w, anc in [
            ("File", 210, "w"), ("Location", 110, "center"),
            ("Category", 140, "w"), ("Term Found", 280, "w"),
            ("Count", 60, "center"),
        ]:
            self.audit_tree.heading(col, text=col)
            self.audit_tree.column(col, width=w, anchor=anc,
                                   stretch=(col in ("File", "Term Found")))

        at_vsb = ttk.Scrollbar(at_frame, orient="vertical",   command=self.audit_tree.yview)
        at_hsb = ttk.Scrollbar(at_frame, orient="horizontal", command=self.audit_tree.xview)
        self.audit_tree.configure(yscrollcommand=at_vsb.set, xscrollcommand=at_hsb.set)
        self.audit_tree.grid(row=0, column=0, sticky="nsew")
        at_vsb.grid(row=0, column=1, sticky="ns")
        at_hsb.grid(row=1, column=0, sticky="ew")
        at_frame.grid_rowconfigure(0, weight=1)
        at_frame.grid_columnconfigure(0, weight=1)

        # Bottom row
        bot = tk.Frame(p, bg=C_WHITE)
        bot.pack(fill="x", pady=(8, 0))
        ttk.Button(bot, text="Export Audit Log (Excel / CSV)",
                   style="Small.TButton", command=self._export_audit).pack(side="left")
        self.audit_status_var = tk.StringVar(value="Run a redaction to populate the audit log.")
        tk.Label(bot, textvariable=self.audit_status_var, bg=C_WHITE,
                 fg=C_MUTED, font=("Segoe UI", 9)).pack(side="left", padx=12)

    # ── Bottom Bar ─────────────────────────────────────────────────────────────

    def _build_bottom_bar(self):
        bar = tk.Frame(self, bg=C_BAR_BG, height=54)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self.process_btn = ttk.Button(bar, text="🚀  Start Redaction",
                                      style="Action.TButton",
                                      command=self._start_redaction)
        self.process_btn.pack(side="right", padx=14, pady=8)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(bar, variable=self.progress_var,
                                            maximum=100, length=260, mode="determinate")
        self.progress_bar.pack(side="right", padx=10, pady=16)

        self.status_var = tk.StringVar(value="Ready — add files and configure rules to begin.")
        tk.Label(bar, textvariable=self.status_var, bg=C_BAR_BG,
                 font=("Segoe UI", 9), fg="#374151").pack(side="left", padx=14)

    # =========================================================================
    # File management
    # =========================================================================

    def _add_files(self):
        exts_str = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        files = filedialog.askopenfilenames(
            title="Select files to redact",
            filetypes=[
                ("All supported", exts_str),
                ("PDF",           "*.pdf"),
                ("Word",          "*.docx"),
                ("Excel",         "*.xlsx"),
                ("Images",        "*.jpg *.jpeg *.png *.tiff *.tif *.bmp"),
                ("All files",     "*.*"),
            ],
        )
        added = sum(1 for f in files if f not in self.file_paths
                    and not self.file_paths.append(f))
        self._refresh_file_tree()
        if added:
            self.status_var.set(f"Added {added} file(s).")

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder to redact (searches recursively)")
        if not folder:
            return
        exts = set(SUPPORTED_EXTENSIONS.keys())
        added = 0
        for f in Path(folder).rglob("*"):
            if f.suffix.lower() in exts and str(f) not in self.file_paths:
                self.file_paths.append(str(f))
                added += 1
        self._refresh_file_tree()
        self.status_var.set(f"Added {added} file(s) from folder.")

    def _remove_selected(self):
        items = self.file_tree.selection()
        indices = sorted([self.file_tree.index(i) for i in items], reverse=True)
        for idx in indices:
            if 0 <= idx < len(self.file_paths):
                self.file_paths.pop(idx)
        self._refresh_file_tree()

    def _clear_files(self):
        self.file_paths.clear()
        self._refresh_file_tree()

    def _browse_output(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.output_dir_var.set(d)

    def _refresh_file_tree(self):
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        for i, fp in enumerate(self.file_paths, 1):
            p = Path(fp)
            ext  = p.suffix.lower().lstrip(".")
            size = p.stat().st_size if p.exists() else 0
            size_str = (f"{size / 1024:.1f} KB" if size < 1_048_576
                        else f"{size / 1_048_576:.1f} MB")
            self.file_tree.insert("", "end",
                                  values=(i, p.name, ext.upper(), size_str, str(p.parent)))
        n = len(self.file_paths)
        self.file_count_var.set(f'{n} file{"s" if n != 1 else ""} added')

    # =========================================================================
    # Keyword management
    # =========================================================================

    def _add_keyword(self):
        kw = self.kw_entry.get().strip()
        if kw and kw not in self.custom_keywords:
            self.custom_keywords.append(kw)
            self.kw_listbox.insert("end", kw)
            self.kw_entry.delete(0, "end")
            self._update_kw_count()

    def _remove_keyword(self):
        sel = list(self.kw_listbox.curselection())
        for idx in sorted(sel, reverse=True):
            self.kw_listbox.delete(idx)
            self.custom_keywords.pop(idx)
        self._update_kw_count()

    def _bulk_import(self):
        raw = self.bulk_text.get("1.0", "end").strip()
        if not raw:
            return
        tokens = [t.strip() for t in re.split(r"[,\n]+", raw) if t.strip()]
        added = 0
        for kw in tokens:
            if kw not in self.custom_keywords:
                self.custom_keywords.append(kw)
                self.kw_listbox.insert("end", kw)
                added += 1
        self.bulk_text.delete("1.0", "end")
        self._update_kw_count()
        self.status_var.set(f"Imported {added} keyword(s).")

    def _update_kw_count(self):
        n = len(self.custom_keywords)
        self.kw_count_var.set(f'{n} keyword{"s" if n != 1 else ""}')

    def _update_preview(self, *_):
        r = self.replacement_var.get() or "[REDACTED]"
        self.preview_lbl.configure(text=f"ABCDE1234F  →  {r}")

    # =========================================================================
    # Profile management
    # =========================================================================

    def _current_rules(self) -> dict:
        return {
            "patterns":       {k: v.get() for k, v in self.pattern_vars.items()},
            "keywords":       list(self.custom_keywords),
            "replacement":    self.replacement_var.get(),
            "case_sensitive": self.case_var.get(),
        }

    def _apply_rules(self, rules: dict):
        for k, v in rules.get("patterns", {}).items():
            if k in self.pattern_vars:
                self.pattern_vars[k].set(v)
        self.custom_keywords.clear()
        self.kw_listbox.delete(0, "end")
        for kw in rules.get("keywords", []):
            self.custom_keywords.append(kw)
            self.kw_listbox.insert("end", kw)
        self.replacement_var.set(rules.get("replacement", "[REDACTED]"))
        self.case_var.set(rules.get("case_sensitive", False))
        self._update_kw_count()

    def _save_profile(self):
        name = self.prof_name_var.get().strip()
        if not name:
            messagebox.showwarning("Profile", "Enter a name for the profile.")
            return
        if self.profile_mgr.save(name, self._current_rules()):
            self._refresh_profile_list()
            self.status_var.set(f'Profile "{name}" saved.')
        else:
            messagebox.showerror("Profile", "Failed to save profile.")

    def _load_profile(self):
        sel = self.prof_tree.selection()
        if not sel:
            messagebox.showinfo("Profile", "Select a profile from the list.")
            return
        name = self.prof_tree.item(sel[0])["values"][0]
        rules = self.profile_mgr.load(name)
        if rules:
            self._apply_rules(rules)
            self.status_var.set(f'Profile "{name}" loaded.')
            self.nb.select(1)
        else:
            messagebox.showerror("Profile", f'Could not load profile: "{name}"')

    def _delete_profile(self):
        sel = self.prof_tree.selection()
        if not sel:
            return
        name = self.prof_tree.item(sel[0])["values"][0]
        if messagebox.askyesno("Delete Profile", f'Delete profile "{name}"?'):
            self.profile_mgr.delete(name)
            self._refresh_profile_list()
            self.status_var.set(f'Profile "{name}" deleted.')

    def _refresh_profile_list(self):
        for item in self.prof_tree.get_children():
            self.prof_tree.delete(item)
        for prof in self.profile_mgr.list_profiles():
            created = prof["created"][:10] if prof["created"] else "—"
            self.prof_tree.insert("", "end",
                                  values=(prof["name"], prof["keywords"], created))

    # =========================================================================
    # Redaction engine
    # =========================================================================

    def _start_redaction(self):
        if not self.file_paths:
            messagebox.showwarning("No Files", "Add at least one file before redacting.")
            return

        enabled  = {k: v.get() for k, v in self.pattern_vars.items()}
        patterns = get_active_patterns(enabled)

        if not patterns and not self.custom_keywords:
            messagebox.showwarning(
                "No Rules",
                "No redaction rules defined.\n\n"
                "Enable at least one auto-detect pattern\n"
                "or add a custom keyword in the Rules tab.",
            )
            return

        self.process_btn.configure(state="disabled")
        self.progress_var.set(0)
        self.status_var.set("Starting…")

        thread = threading.Thread(
            target=self._run_redaction,
            args=(patterns,),
            daemon=True,
        )
        thread.start()

    def _run_redaction(self, patterns: list):
        try:
            audit = self.engine.process_files(
                files          = list(self.file_paths),
                patterns       = patterns,
                custom_terms   = list(self.custom_keywords),
                replacement    = self.replacement_var.get() or "[REDACTED]",
                case_sensitive = self.case_var.get(),
                output_dir     = self.output_dir_var.get().strip() or None,
                progress_callback = self._thread_progress,
            )
            self.after(0, self._on_complete, audit)
        except Exception as exc:
            self.after(0, self._on_error, str(exc))

    def _thread_progress(self, done: int, total: int, name: str):
        pct = (done / total * 100) if total else 0
        self.after(0, self.progress_var.set, pct)
        self.after(0, self.status_var.set, f"Processing {done}/{total}: {name}")

    def _on_complete(self, audit: list):
        self.audit_entries = audit
        self._refresh_audit_log()
        self.progress_var.set(100)
        self.process_btn.configure(state="normal")

        total = sum(e.get("Count", 0) for e in audit)
        files = len(self.file_paths)

        out_loc = self.output_dir_var.get().strip() or "_redacted  subfolder beside each file"
        self.status_var.set(
            f"✓ Done — {files} file(s) processed, {total} redaction(s) applied."
        )
        self.nb.select(3)  # Switch to Audit Log tab

        messagebox.showinfo(
            "Redaction Complete",
            f"✅  Redaction finished!\n\n"
            f"  Files processed : {files}\n"
            f"  Total redactions: {total}\n\n"
            f"  Output location:\n  {out_loc}",
        )

    def _on_error(self, err: str):
        self.process_btn.configure(state="normal")
        self.status_var.set("Error — see message box.")
        messagebox.showerror("Redaction Error", f"An error occurred:\n\n{err}")

    # =========================================================================
    # Audit log
    # =========================================================================

    def _refresh_audit_log(self):
        for item in self.audit_tree.get_children():
            self.audit_tree.delete(item)

        # Alternating row colours
        self.audit_tree.tag_configure("odd",  background="#f9fafb")
        self.audit_tree.tag_configure("even", background=C_WHITE)

        for i, e in enumerate(self.audit_entries):
            tag = "odd" if i % 2 else "even"
            self.audit_tree.insert(
                "", "end",
                values=(
                    e.get("File", ""),
                    e.get("Page", ""),
                    e.get("Category", ""),
                    e.get("Original Term", ""),
                    e.get("Count", 0),
                ),
                tags=(tag,),
            )

        # KPI counts
        total   = sum(e.get("Count", 0) for e in self.audit_entries)
        files   = len({e.get("File") for e in self.audit_entries})
        pan_tan = sum(e.get("Count", 0) for e in self.audit_entries
                      if e.get("Category") in ("PAN Number", "TAN Number"))
        gst_cin = sum(e.get("Count", 0) for e in self.audit_entries
                      if e.get("Category") in ("GSTIN", "CIN"))
        aadhaar = sum(e.get("Count", 0) for e in self.audit_entries
                      if e.get("Category") in ("Aadhaar Number", "Phone Number"))
        custom  = sum(e.get("Count", 0) for e in self.audit_entries
                      if e.get("Category") == "Custom Keyword")

        self._kpi_vars["files"].set(str(files))
        self._kpi_vars["total"].set(str(total))
        self._kpi_vars["pan_tan"].set(str(pan_tan))
        self._kpi_vars["gst_cin"].set(str(gst_cin))
        self._kpi_vars["aadhaar"].set(str(aadhaar))
        self._kpi_vars["custom"].set(str(custom))

        n = len(self.audit_entries)
        self.audit_status_var.set(f'{n} redaction event{"s" if n != 1 else ""}  |  '
                                  f'{total} total replacement{"s" if total != 1 else ""}')

    def _export_audit(self):
        if not self.audit_entries:
            messagebox.showinfo("Export", "No audit data to export yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Audit Log",
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx"), ("CSV file", "*.csv")],
        )
        if not path:
            return
        try:
            import pandas as pd
            df = pd.DataFrame(self.audit_entries)
            if path.endswith(".csv"):
                df.to_csv(path, index=False, encoding="utf-8-sig")
            else:
                df.to_excel(path, index=False, engine="openpyxl")
            self.status_var.set(f"Audit log exported → {Path(path).name}")
        except ImportError:
            messagebox.showerror("Export Error", "pandas is required for export.\npip install pandas openpyxl")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = RedactApp()
    app.mainloop()
