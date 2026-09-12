"""Tkinter GUI front-end for AutoStamp."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import stamper
from .stamper import StampOptions

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_STAMP_IMAGE = ASSETS_DIR / "as_built_stamp.png"


class AutoStampApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoStamp - AS BUILT Mark Up Stamper")
        self.geometry("820x680")
        self.minsize(760, 620)

        self.files: list[str] = []
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.worker_running = False
        self._closing = False
        self._poll_job = None

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_job = self.after(150, self._poll_log_queue)

    def _on_close(self):
        if self.worker_running:
            if not messagebox.askyesno(
                    "AutoStamp",
                    "Stamping is still in progress. Close anyway?\n"
                    "(Files already processed are safely saved.)"):
                return
        self._closing = True
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
        self.destroy()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        # --- File list -----------------------------------------------------
        files_frame = ttk.LabelFrame(self, text="PDF documents to stamp")
        files_frame.pack(fill="both", expand=False, **pad)

        list_row = ttk.Frame(files_frame)
        list_row.pack(fill="both", expand=True, padx=6, pady=6)

        self.file_listbox = tk.Listbox(list_row, selectmode="extended", height=6)
        self.file_listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_row, command=self.file_listbox.yview)
        scroll.pack(side="left", fill="y")
        self.file_listbox.config(yscrollcommand=scroll.set)

        btns = ttk.Frame(files_frame)
        btns.pack(side="left", fill="y", padx=6, pady=6)
        ttk.Button(btns, text="Add PDFs...", command=self._add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="Remove Selected", command=self._remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="Clear All", command=self._clear_files).pack(fill="x", pady=2)

        # --- Stamp image -----------------------------------------------------
        stamp_frame = ttk.LabelFrame(self, text="Stamp image")
        stamp_frame.pack(fill="x", **pad)

        self.stamp_path_var = tk.StringVar(value=str(DEFAULT_STAMP_IMAGE))
        ttk.Entry(stamp_frame, textvariable=self.stamp_path_var).pack(
            side="left", fill="x", expand=True, padx=6, pady=6)
        ttk.Button(stamp_frame, text="Browse...", command=self._browse_stamp).pack(
            side="left", padx=6, pady=6)

        # --- Size / pages -----------------------------------------------------
        size_frame = ttk.LabelFrame(self, text="Stamp size (fixed on every page; shrinks only if a page is too packed to fit it)")
        size_frame.pack(fill="x", **pad)

        ttk.Label(size_frame, text="Width (inches):").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.width_var = tk.DoubleVar(value=stamper.DEFAULT_STAMP_WIDTH_IN)
        width_spin = ttk.Spinbox(size_frame, from_=0.5, to=6.0, increment=0.05,
                                  textvariable=self.width_var, width=8,
                                  command=self._update_height_label)
        width_spin.grid(row=0, column=1, sticky="w", padx=6, pady=4)
        width_spin.bind("<KeyRelease>", lambda e: self._update_height_label())

        self.height_label_var = tk.StringVar(value="")
        ttk.Label(size_frame, textvariable=self.height_label_var).grid(
            row=0, column=2, sticky="w", padx=6, pady=4)

        ttk.Label(size_frame, text="Pages to stamp:").grid(row=0, column=3, sticky="w", padx=(24, 6), pady=4)
        self.pages_var = tk.StringVar(value="all")
        ttk.Entry(size_frame, textvariable=self.pages_var, width=14).grid(
            row=0, column=4, sticky="w", padx=6, pady=4)
        ttk.Label(size_frame, text="e.g. all, 2, 1-2,4").grid(
            row=0, column=5, sticky="w", padx=(0, 6), pady=4)

        self._update_height_label()

        # --- Advanced placement options -----------------------------------
        adv = ttk.LabelFrame(self, text="Placement rules (advanced)")
        adv.pack(fill="x", **pad)

        self.margin_var = tk.DoubleVar(value=stamper.PAGE_MARGIN_IN)
        self.top_excl_var = tk.DoubleVar(value=stamper.TOP_EXCLUDE_IN)
        self.bottom_excl_var = tk.DoubleVar(value=stamper.BOTTOM_EXCLUDE_IN)
        self.gap_var = tk.DoubleVar(value=stamper.GAP_IN)
        self.dpi_var = tk.IntVar(value=stamper.DEFAULT_DPI)

        def labeled_spin(parent, row, col, text, var, frm, to, inc):
            ttk.Label(parent, text=text).grid(row=row, column=col, sticky="w", padx=6, pady=4)
            ttk.Spinbox(parent, from_=frm, to=to, increment=inc, textvariable=var,
                        width=7).grid(row=row, column=col + 1, sticky="w", padx=(0, 18), pady=4)

        labeled_spin(adv, 0, 0, "Page margin (in):", self.margin_var, 0.0, 1.0, 0.05)
        labeled_spin(adv, 0, 2, "Keep clear of top (in):", self.top_excl_var, 0.0, 2.0, 0.05)
        labeled_spin(adv, 1, 0, "Keep clear of bottom / title block (in):", self.bottom_excl_var, 0.0, 4.0, 0.1)
        labeled_spin(adv, 1, 2, "Gap next to existing stamps (in):", self.gap_var, 0.0, 1.0, 0.05)

        self.never_skip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            adv, text="Never skip a page - shrink the stamp (or use the least-obstructive\n"
                      "spot as a last resort) rather than leave a page unstamped",
            variable=self.never_skip_var).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))

        self.min_width_var = tk.DoubleVar(value=stamper.MIN_STAMP_WIDTH_IN)
        labeled_spin(adv, 3, 0, "Smallest the stamp may shrink to (in):", self.min_width_var, 0.4, 3.0, 0.1)

        # --- Output -----------------------------------------------------
        out_frame = ttk.LabelFrame(self, text="Output")
        out_frame.pack(fill="x", **pad)

        self.output_mode = tk.StringVar(value="alongside")
        ttk.Radiobutton(out_frame, text="Save next to original (adds _STAMPED suffix)",
                        variable=self.output_mode, value="alongside",
                        command=self._toggle_output_folder).grid(row=0, column=0, columnspan=3,
                                                                  sticky="w", padx=6, pady=2)
        ttk.Radiobutton(out_frame, text="Save to folder:", variable=self.output_mode,
                        value="folder", command=self._toggle_output_folder).grid(
            row=1, column=0, sticky="w", padx=6, pady=2)
        self.output_dir_var = tk.StringVar(value="")
        self.output_dir_entry = ttk.Entry(out_frame, textvariable=self.output_dir_var, state="disabled")
        self.output_dir_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        out_frame.columnconfigure(1, weight=1)
        self.output_dir_button = ttk.Button(out_frame, text="Browse...",
                                             command=self._browse_output_dir, state="disabled")
        self.output_dir_button.grid(row=1, column=2, padx=6, pady=2)

        # --- Action buttons -----------------------------------------------------
        action_frame = ttk.Frame(self)
        action_frame.pack(fill="x", **pad)
        ttk.Button(action_frame, text="Preview First Page",
                   command=self._preview).pack(side="left", padx=4)
        self.run_button = ttk.Button(action_frame, text="Stamp All Documents",
                                      command=self._run_stamping)
        self.run_button.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(action_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=12)

        # --- Log -----------------------------------------------------
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side="left", fill="y")
        self.log_text.config(yscrollcommand=log_scroll.set)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _update_height_label(self):
        try:
            width_in = float(self.width_var.get())
            _, height_in = stamper.get_stamp_size_in(self.stamp_path_var.get(), width_in)
            self.height_label_var.set(f"(height auto: {height_in:.2f} in)")
        except Exception:
            self.height_label_var.set("")

    def _toggle_output_folder(self):
        state = "normal" if self.output_mode.get() == "folder" else "disabled"
        self.output_dir_entry.config(state=state)
        self.output_dir_button.config(state=state)

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select PDF documents", filetypes=[("PDF files", "*.pdf")])
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.file_listbox.insert("end", p)

    def _remove_selected(self):
        for idx in reversed(self.file_listbox.curselection()):
            self.file_listbox.delete(idx)
            del self.files[idx]

    def _clear_files(self):
        self.file_listbox.delete(0, "end")
        self.files.clear()

    def _browse_stamp(self):
        p = filedialog.askopenfilename(
            title="Select stamp image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if p:
            self.stamp_path_var.set(p)
            self._update_height_label()

    def _browse_output_dir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.output_dir_var.set(d)

    def _log(self, msg: str):
        self.log_queue.put(msg)

    def _poll_log_queue(self):
        if self._closing:
            return
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self._poll_job = self.after(150, self._poll_log_queue)

    def _build_options(self) -> StampOptions:
        return StampOptions(
            stamp_image_path=self.stamp_path_var.get(),
            stamp_width_in=float(self.width_var.get()),
            dpi=int(self.dpi_var.get()),
            pages=self.pages_var.get(),
            margin_in=float(self.margin_var.get()),
            top_exclude_in=float(self.top_excl_var.get()),
            bottom_exclude_in=float(self.bottom_excl_var.get()),
            gap_in=float(self.gap_var.get()),
            never_skip=bool(self.never_skip_var.get()),
            min_stamp_width_in=float(self.min_width_var.get()),
        )

    def _output_path_for(self, input_path: str) -> str:
        p = Path(input_path)
        if self.output_mode.get() == "folder" and self.output_dir_var.get():
            return str(Path(self.output_dir_var.get()) / f"{p.stem}_STAMPED{p.suffix}")
        return str(p.with_name(f"{p.stem}_STAMPED{p.suffix}"))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _preview(self):
        if not self.files:
            messagebox.showinfo("AutoStamp", "Add at least one PDF first.")
            return
        if not os.path.isfile(self.stamp_path_var.get()):
            messagebox.showerror("AutoStamp", "Stamp image not found.")
            return
        try:
            opts = self._build_options()
            img = stamper.preview_page(self.files[0], 1, opts)
        except Exception as exc:
            messagebox.showerror("AutoStamp", f"Preview failed:\n{exc}")
            return

        win = tk.Toplevel(self)
        win.title(f"Preview: {Path(self.files[0]).name} (page 1)")
        img.thumbnail((1000, 1300))
        photo = ImageTk.PhotoImage(img)
        label = tk.Label(win, image=photo)
        label.image = photo  # keep reference
        label.pack()

    def _run_stamping(self):
        if self.worker_running:
            return
        if not self.files:
            messagebox.showinfo("AutoStamp", "Add at least one PDF first.")
            return
        if not os.path.isfile(self.stamp_path_var.get()):
            messagebox.showerror("AutoStamp", "Stamp image not found.")
            return
        if self.output_mode.get() == "folder" and not self.output_dir_var.get():
            messagebox.showinfo("AutoStamp", "Choose an output folder, or switch to 'Save next to original'.")
            return

        opts = self._build_options()
        jobs = [(f, self._output_path_for(f)) for f in self.files]

        self.worker_running = True
        self.run_button.config(state="disabled")
        self.progress.config(maximum=len(jobs), value=0)

        thread = threading.Thread(target=self._worker, args=(jobs, opts), daemon=True)
        thread.start()

    def _worker(self, jobs, opts: StampOptions):
        clean_total = 0
        tight_total = 0
        skipped_total = 0
        failed = 0
        for i, (in_path, out_path) in enumerate(jobs, start=1):
            self._log(f"[{i}/{len(jobs)}] {Path(in_path).name}")
            result = stamper.stamp_document(in_path, out_path, opts, log=self._log)
            if result.error:
                failed += 1
                self._log(f"  FAILED: {result.error}")
            else:
                for pr in result.pages:
                    if not pr.placed:
                        skipped_total += 1
                    elif pr.tight_fit:
                        tight_total += 1
                    else:
                        clean_total += 1
                self._log(f"  saved -> {result.output_path}")
            if self._closing:
                return
            self.after(0, self._advance_progress, i)

        self._log(f"Done. {clean_total} page(s) stamped cleanly, {tight_total} needed a tight "
                   f"fit (page was densely packed), {skipped_total} skipped, {failed} file(s) failed.")
        if not self._closing:
            self.after(0, self._finish_run)
        self.worker_running = False

    def _advance_progress(self, value):
        if self._closing:
            return
        self.progress.config(value=value)

    def _finish_run(self):
        self.worker_running = False
        self.run_button.config(state="normal")
        messagebox.showinfo("AutoStamp", "Stamping complete. See the log for details.")


def main():
    app = AutoStampApp()
    app.mainloop()


if __name__ == "__main__":
    main()
