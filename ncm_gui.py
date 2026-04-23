"""
NCM 转换工具 - 图形界面
支持:
  - 拖放文件 / 文件夹
  - 批量转换
  - 自定义输出目录
  - 实时进度 + 日志
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# ---- 尝试加载拖放支持 ----
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

from ncm_core import convert

# ═══════════════════════════════════════════════════════
#  配色方案 (GitHub Dark 风格)
# ═══════════════════════════════════════════════════════
C = {
    'bg':       '#0d1117',
    'surface':  '#161b22',
    'border':   '#30363d',
    'hover':    '#1f6feb',
    'accent':   '#58a6ff',
    'accent2':  '#bc8cff',
    'success':  '#3fb950',
    'warning':  '#d29922',
    'danger':   '#f85149',
    'fg':       '#e6edf3',
    'fg2':      '#8b949e',
    'fg3':      '#6e7681',
    'input':    '#0d1117',
    'progress': '#1f6feb',
    'btn_bg':   '#21262d',
    'btn_fg':   '#c9d1d9',
}

FONT = {
    'title':    ('Segoe UI', 18, 'bold'),
    'h2':       ('Segoe UI', 12, 'bold'),
    'body':     ('Segoe UI', 10),
    'small':    ('Segoe UI', 9),
    'mono':     ('Cascadia Code', 9),
    'btn':      ('Segoe UI', 10),
    'btn_lg':   ('Segoe UI', 11, 'bold'),
    'stat':     ('Segoe UI', 28, 'bold'),
    'stat_lbl': ('Segoe UI', 9),
}


# ═══════════════════════════════════════════════════════
#  主应用
# ═══════════════════════════════════════════════════════
class NcmConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title('NCM Converter')
        self.root.geometry('960x780')
        self.root.minsize(780, 640)
        self.root.configure(bg=C['bg'])
        self.root.resizable(True, True)

        self.files = []
        self.output_dir = tk.StringVar(value='')
        self.running = False
        self._stop_flag = False
        self._ok_count = 0
        self._err_count = 0

        self._setup_styles()
        self._build_ui()
        self._bind_dnd()

    # ─────────────── 样式 ───────────────
    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use('default')

        s.configure('Modern.Treeview',
                     background=C['surface'], foreground=C['fg'],
                     fieldbackground=C['surface'], rowheight=32,
                     font=FONT['body'], borderwidth=0)
        s.configure('Modern.Treeview.Heading',
                     background=C['border'], foreground=C['fg2'],
                     font=FONT['small'], borderwidth=0, relief='flat')
        s.map('Modern.Treeview',
              background=[('selected', C['hover'])],
              foreground=[('selected', '#ffffff')])
        s.map('Modern.Treeview.Heading',
              background=[('active', C['hover'])])

        s.configure('Modern.Horizontal.TProgressbar',
                     troughcolor=C['border'], background=C['progress'],
                     borderwidth=0, thickness=6)

        s.configure('Modern.Vertical.TScrollbar',
                     background=C['border'], troughcolor=C['surface'],
                     borderwidth=0, arrowsize=0)
        s.map('Modern.Vertical.TScrollbar',
              background=[('active', C['hover'])])

    # ═══════════════════════════════════════════════════
    #  UI 构建
    # ═══════════════════════════════════════════════════
    def _build_ui(self):
        self._build_header()
        self._build_body()
        self._build_statusbar()

    # ─────────────── Header ───────────────
    def _build_header(self):
        hdr = tk.Frame(self.root, bg=C['surface'], height=56)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)

        left = tk.Frame(hdr, bg=C['surface'])
        left.pack(side='left', fill='y', padx=20)

        # macOS 风格红黄绿圆点
        dots = tk.Frame(left, bg=C['surface'])
        dots.pack(side='left', padx=(0, 12), pady=18)
        for color in [C['danger'], C['warning'], C['success']]:
            cv = tk.Canvas(dots, width=12, height=12, bg=C['surface'], highlightthickness=0)
            cv.pack(side='left', padx=2)
            cv.create_oval(2, 2, 10, 10, fill=color, outline='')

        tk.Label(left, text='NCM Converter',
                 font=FONT['title'], bg=C['surface'], fg=C['fg']).pack(side='left')

        tk.Frame(left, width=1, bg=C['border']).pack(side='left', fill='y', padx=16, pady=14)

        tk.Label(left, text='网易云音乐 NCM 解密工具',
                 font=FONT['body'], bg=C['surface'], fg=C['fg2']).pack(side='left')

        tk.Label(hdr, text='v1.0',
                 font=FONT['small'], bg=C['surface'], fg=C['fg3']).pack(side='right', padx=20)

    # ─────────────── Body ───────────────
    def _build_body(self):
        body = tk.Frame(self.root, bg=C['bg'])
        body.pack(fill='both', expand=True, padx=16, pady=(12, 0))

        self._build_file_area(body)

        bottom = tk.Frame(body, bg=C['bg'])
        bottom.pack(fill='both', expand=True, pady=(10, 0))

        self._build_controls(bottom)
        self._build_log(bottom)

    # ─────────────── 文件区 ───────────────
    def _build_file_area(self, parent):
        card = tk.Frame(parent, bg=C['surface'], highlightbackground=C['border'],
                         highlightthickness=1)
        card.pack(fill='both', expand=True)

        header = tk.Frame(card, bg=C['surface'])
        header.pack(fill='x', padx=16, pady=(12, 0))

        tk.Label(header, text='文件列表', font=FONT['h2'],
                 bg=C['surface'], fg=C['fg']).pack(side='left')

        btn_frame = tk.Frame(header, bg=C['surface'])
        btn_frame.pack(side='right')

        for txt, cmd, style in [
            ('添加文件', self._add_files, 'default'),
            ('添加文件夹', self._add_folder, 'default'),
            ('移除选中', self._remove_selected, 'ghost'),
            ('清空', self._clear_list, 'danger'),
        ]:
            self._make_btn(btn_frame, txt, cmd, style).pack(side='left', padx=2)

        # 输出目录行
        out_row = tk.Frame(card, bg=C['surface'])
        out_row.pack(fill='x', padx=16, pady=(10, 0))

        tk.Label(out_row, text='输出至', font=FONT['small'],
                 bg=C['surface'], fg=C['fg2']).pack(side='left')

        self.out_entry = tk.Entry(out_row, textvariable=self.output_dir, font=FONT['body'],
                                   bg=C['input'], fg=C['fg'], insertbackground=C['fg'],
                                   bd=0, relief='flat', highlightthickness=1,
                                   highlightbackground=C['border'], highlightcolor=C['accent'])
        self.out_entry.pack(side='left', fill='x', expand=True, padx=(8, 0), ipady=4)

        self._make_btn(out_row, '浏览', self._choose_output, 'ghost').pack(side='left', padx=(6, 0))

        tk.Label(out_row, text='留空则输出到原目录', font=FONT['small'],
                 bg=C['surface'], fg=C['fg3']).pack(side='left', padx=(8, 0))

        # Treeview
        tree_frame = tk.Frame(card, bg=C['surface'])
        tree_frame.pack(fill='both', expand=True, padx=16, pady=(10, 12))

        cols = ('idx', 'name', 'size', 'status')
        self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings',
                                  selectmode='extended', height=8, style='Modern.Treeview')
        self.tree.heading('idx', text='#')
        self.tree.heading('name', text='文件名')
        self.tree.heading('size', text='大小')
        self.tree.heading('status', text='状态')
        self.tree.column('idx', width=36, anchor='center', stretch=False)
        self.tree.column('name', width=360, anchor='w')
        self.tree.column('size', width=80, anchor='center')
        self.tree.column('status', width=90, anchor='center')

        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview,
                             style='Modern.Vertical.TScrollbar')
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)

        self.drop_hint = tk.Label(card, text='拖放 .ncm 文件到列表即可添加',
                                   font=FONT['small'], bg=C['surface'], fg=C['fg3'], pady=4)
        self.drop_hint.pack(fill='x', padx=16, pady=(0, 8))

    # ─────────────── 控制区 ───────────────
    def _build_controls(self, parent):
        card = tk.Frame(parent, bg=C['surface'], highlightbackground=C['border'],
                         highlightthickness=1)
        card.pack(side='left', fill='both')
        card.configure(width=280)
        card.pack_propagate(False)

        tk.Label(card, text='转换控制', font=FONT['h2'],
                 bg=C['surface'], fg=C['fg']).pack(anchor='w', padx=16, pady=(16, 0))

        self.btn_start = tk.Button(
            card, text='开始转换', command=self._start_convert,
            bg=C['accent'], fg='#ffffff', activebackground=C['hover'], activeforeground='#ffffff',
            font=FONT['btn_lg'], bd=0, relief='flat', cursor='hand2', pady=8)
        self.btn_start.pack(fill='x', padx=16, pady=(16, 6))

        self.btn_stop = tk.Button(
            card, text='停止', command=self._stop_convert,
            bg=C['btn_bg'], fg=C['fg2'], activebackground=C['hover'], activeforeground='#ffffff',
            font=FONT['btn_lg'], bd=0, relief='flat', cursor='hand2', pady=8,
            state='disabled')
        self.btn_stop.pack(fill='x', padx=16, pady=(0, 0))

        tk.Frame(card, height=1, bg=C['border']).pack(fill='x', padx=16, pady=(16, 0))

        pg1 = tk.Frame(card, bg=C['surface'])
        pg1.pack(fill='x', padx=16, pady=(12, 0))
        tk.Label(pg1, text='总进度', font=FONT['small'], bg=C['surface'], fg=C['fg2']).pack(side='left')
        self.lbl_total_pct = tk.Label(pg1, text='0%', font=FONT['small'], bg=C['surface'], fg=C['accent'])
        self.lbl_total_pct.pack(side='right')
        self.progress_total = ttk.Progressbar(card, mode='determinate', length=240,
                                               style='Modern.Horizontal.TProgressbar')
        self.progress_total.pack(fill='x', padx=16, pady=(4, 0))

        pg2 = tk.Frame(card, bg=C['surface'])
        pg2.pack(fill='x', padx=16, pady=(10, 0))
        tk.Label(pg2, text='当前文件', font=FONT['small'], bg=C['surface'], fg=C['fg2']).pack(side='left')
        self.lbl_file_pct = tk.Label(pg2, text='0%', font=FONT['small'], bg=C['surface'], fg=C['accent'])
        self.lbl_file_pct.pack(side='right')
        self.progress_file = ttk.Progressbar(card, mode='determinate', length=240,
                                               style='Modern.Horizontal.TProgressbar')
        self.progress_file.pack(fill='x', padx=16, pady=(4, 0))

        self.lbl_current = tk.Label(card, text='—', font=FONT['small'],
                                    bg=C['surface'], fg=C['fg3'], anchor='w', wraplength=240)
        self.lbl_current.pack(anchor='w', padx=16, pady=(4, 0))

        tk.Frame(card, height=1, bg=C['border']).pack(fill='x', padx=16, pady=(16, 0))

        stats = tk.Frame(card, bg=C['surface'])
        stats.pack(fill='x', padx=16, pady=(12, 16))

        for i, (label, color) in enumerate([
            ('待处理', C['fg2']),
            ('已完成', C['success']),
            ('失败', C['danger']),
        ]):
            col = tk.Frame(stats, bg=C['surface'])
            col.pack(side='left', expand=True, fill='x')
            lbl_val = tk.Label(col, text='0', font=FONT['stat'], bg=C['surface'], fg=color)
            lbl_val.pack()
            tk.Label(col, text=label, font=FONT['stat_lbl'], bg=C['surface'], fg=C['fg3']).pack()
            if i == 0:
                self.lbl_stat_pending = lbl_val
            elif i == 1:
                self.lbl_stat_ok = lbl_val
            else:
                self.lbl_stat_err = lbl_val

    # ─────────────── 日志区 ───────────────
    def _build_log(self, parent):
        card = tk.Frame(parent, bg=C['surface'], highlightbackground=C['border'],
                         highlightthickness=1)
        card.pack(side='right', fill='both', expand=True, padx=(10, 0))

        header = tk.Frame(card, bg=C['surface'])
        header.pack(fill='x', padx=16, pady=(12, 0))
        tk.Label(header, text='转换日志', font=FONT['h2'], bg=C['surface'], fg=C['fg']).pack(side='left')

        self.log = tk.Text(card, font=FONT['mono'], bg=C['bg'], fg=C['fg2'],
                            state='disabled', wrap='word', bd=0, padx=12, pady=8,
                            insertbackground=C['fg'], selectbackground=C['hover'],
                            selectforeground='#ffffff', relief='flat')
        vsb = ttk.Scrollbar(card, orient='vertical', command=self.log.yview,
                             style='Modern.Vertical.TScrollbar')
        self.log.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y', pady=(8, 8))
        self.log.pack(fill='both', expand=True, padx=(0, 0), pady=(8, 8))

        self.log.tag_config('ok', foreground=C['success'])
        self.log.tag_config('err', foreground=C['danger'])
        self.log.tag_config('info', foreground=C['accent'])
        self.log.tag_config('warn', foreground=C['warning'])

    # ─────────────── 状态栏 ───────────────
    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=C['surface'], height=28)
        bar.pack(fill='x', side='bottom')
        bar.pack_propagate(False)

        self.status_dot = tk.Canvas(bar, width=8, height=8, bg=C['surface'], highlightthickness=0)
        self.status_dot.pack(side='left', padx=(12, 6), pady=10)
        self.dot_id = self.status_dot.create_oval(0, 0, 8, 8, fill=C['success'], outline='')

        self.status_var = tk.StringVar(value='就绪')
        tk.Label(bar, textvariable=self.status_var, font=FONT['small'],
                 bg=C['surface'], fg=C['fg2'], anchor='w').pack(side='left', fill='y')

    # ═══════════════════════════════════════════════════
    #  普通按钮工厂
    # ═══════════════════════════════════════════════════
    def _make_btn(self, parent, text, cmd, style='default'):
        colors = {
            'default': (C['btn_bg'], C['btn_fg']),
            'ghost':   (C['surface'], C['fg2']),
            'danger':  (C['surface'], C['danger']),
        }
        bg, fg = colors.get(style, colors['default'])
        hover_bg = C['hover'] if style != 'danger' else C['danger']

        btn = tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=hover_bg, activeforeground='#ffffff',
                         font=FONT['btn'], bd=0, relief='flat', cursor='hand2',
                         padx=12, pady=4)
        return btn

    # ═══════════════════════════════════════════════════
    #  拖放
    # ═══════════════════════════════════════════════════
    def _bind_dnd(self):
        if HAS_DND:
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind('<<Drop>>', self._on_drop)
            self.drop_hint.configure(text='拖放 .ncm 文件到列表即可添加')
        else:
            self.drop_hint.configure(text='安装 tkinterdnd2 可启用拖放功能')

    def _on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        self._add_paths(list(paths))

    # ═══════════════════════════════════════════════════
    #  文件操作
    # ═══════════════════════════════════════════════════
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title='选择 NCM 文件',
            filetypes=[('NCM 文件', '*.ncm'), ('所有文件', '*.*')])
        self._add_paths(list(paths))

    def _add_folder(self):
        folder = filedialog.askdirectory(title='选择包含 NCM 文件的文件夹')
        if folder:
            paths = list(Path(folder).rglob('*.ncm'))
            self._add_paths([str(p) for p in paths])

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            p = os.path.normpath(p)
            if p.lower().endswith('.ncm') and p not in self.files:
                self.files.append(p)
                try:
                    size = self._fmt_size(os.path.getsize(p))
                except OSError:
                    size = '—'
                idx = len(self.files)
                self.tree.insert('', 'end', iid=p, values=(idx, os.path.basename(p), size, '等待中'))
                added += 1
        if added:
            self._log(f'已添加 {added} 个文件，共 {len(self.files)} 个待转换', 'info')
        self._update_stats()

    def _remove_selected(self):
        sel = self.tree.selection()
        for iid in sel:
            self.tree.delete(iid)
            if iid in self.files:
                self.files.remove(iid)
        self._reindex()
        self._update_stats()

    def _clear_list(self):
        self.tree.delete(*self.tree.get_children())
        self.files.clear()
        self._update_stats()

    def _reindex(self):
        for i, item in enumerate(self.tree.get_children()):
            vals = list(self.tree.item(item, 'values'))
            vals[0] = i + 1
            self.tree.item(item, values=vals)

    def _choose_output(self):
        folder = filedialog.askdirectory(title='选择输出目录')
        if folder:
            self.out_entry.delete(0, 'end')
            self.out_entry.insert(0, folder)

    # ═══════════════════════════════════════════════════
    #  转换逻辑
    # ═══════════════════════════════════════════════════
    def _start_convert(self):
        if not self.files:
            messagebox.showwarning('提示', '请先添加 NCM 文件！')
            return
        if self.running:
            return
        self.running = True
        self._stop_flag = False
        self._ok_count = 0
        self._err_count = 0
        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.progress_total['value'] = 0
        self.progress_file['value'] = 0
        self._set_status('转换中…', running=True)

        out_dir = self.out_entry.get().strip() or None
        thread = threading.Thread(target=self._convert_all,
                                   args=(list(self.files), out_dir), daemon=True)
        thread.start()

    def _stop_convert(self):
        self._stop_flag = True
        self._log('正在停止，当前文件完成后将中止…', 'warn')

    def _convert_all(self, files, out_dir):
        total = len(files)
        for idx, path in enumerate(files):
            if self._stop_flag:
                break
            basename = os.path.basename(path)
            self._set_status(f'({idx + 1}/{total}) {basename}', running=True)
            self._update_item(path, '转换中…')
            self.lbl_current.config(text=basename)
            self.progress_file['value'] = 0

            def _progress(done, total_bytes):
                pct = min(int(done / total_bytes * 100), 100) if total_bytes else 0
                self.root.after(0, lambda p=pct: self._set_file_progress(p))

            try:
                out = convert(path, out_dir, progress_cb=_progress)
                self._ok_count += 1
                self.root.after(0, lambda p=path, o=out: (
                    self._update_item(p, '已完成'),
                    self._log(f'{os.path.basename(p)} → {os.path.basename(o)}', 'ok')))
            except Exception as e:
                self._err_count += 1
                err_msg = str(e)
                self.root.after(0, lambda p=path, e=err_msg: (
                    self._update_item(p, '失败'),
                    self._log(f'{os.path.basename(p)}: {e}', 'err')))

            total_pct = int((idx + 1) / total * 100)
            self.root.after(0, lambda p=total_pct: self.progress_total.configure(value=p))
            self.root.after(0, lambda p=total_pct: self.lbl_total_pct.config(text=f'{p}%'))

        self.root.after(0, self._on_done)

    def _on_done(self):
        self.running = False
        self.btn_start.config(state='normal')
        self.btn_stop.config(state='disabled')
        total = self._ok_count + self._err_count
        msg = f'完成！共 {total} 个，成功 {self._ok_count} 个，失败 {self._err_count} 个'
        self._set_status(msg)
        self._log(msg, 'info')
        self._update_stats()
        self.progress_file['value'] = 100
        self.lbl_file_pct.config(text='100%')
        if self._err_count == 0:
            messagebox.showinfo('完成', msg)
        else:
            messagebox.showwarning('完成（有失败）', msg)

    # ═══════════════════════════════════════════════════
    #  UI 更新
    # ═══════════════════════════════════════════════════
    def _update_item(self, iid, status):
        if self.tree.exists(iid):
            vals = list(self.tree.item(iid, 'values'))
            vals[3] = status
            self.tree.item(iid, values=vals)

    def _update_stats(self):
        self.lbl_stat_pending.config(text=str(len(self.files)))
        self.lbl_stat_ok.config(text=str(self._ok_count))
        self.lbl_stat_err.config(text=str(self._err_count))

    def _set_status(self, text, running=False):
        def _do():
            self.status_var.set(text)
            color = C['accent'] if running else C['success']
            self.status_dot.itemconfig(self.dot_id, fill=color)
        self.root.after(0, _do)

    def _set_file_progress(self, pct):
        self.progress_file['value'] = pct
        self.lbl_file_pct.config(text=f'{pct}%')

    def _log(self, msg, tag=''):
        def _do():
            self.log.config(state='normal')
            self.log.insert('end', msg + '\n', tag)
            self.log.see('end')
            self.log.config(state='disabled')
        self.root.after(0, _do)

    @staticmethod
    def _fmt_size(n):
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n < 1024:
                return f'{n:.1f} {unit}'
            n /= 1024
        return f'{n:.1f} TB'


# ═══════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════
def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    app = NcmConverterApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
