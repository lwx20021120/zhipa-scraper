# -*- coding: utf-8 -*-
"""主窗口：AI 自然语言抓取 + 手动抓取 + 数据预览 + 导出 + 设置。"""
import io
import threading
from pathlib import Path

import flet as ft
import pandas as pd

from ..scraper import scrape, auto_fetch, ScrapeError
from ..validator import run_ai_scrape
from ..config import load_config, save_config
from .. import exporter, history, last_result

# 类型选项：键 -> 显示名
TYPE_OPTIONS = [("text", "文本"), ("attr", "属性"), ("html", "HTML"),
                ("json", "JSON接口"), ("image", "图片")]
# 抓取器选项
FETCHER_OPTIONS = [("auto", "自动"), ("static", "静态"), ("dynamic", "动态"), ("stealthy", "伪装")]

EXPORT_FORMATS = {
    "csv": ("抓取结果.csv", ["csv"], exporter.export_csv),
    "excel": ("抓取结果.xlsx", ["xlsx"], exporter.export_excel),
    "json": ("抓取结果.json", ["json"], exporter.export_json),
}


def _build_export_bytes(rows: list, fmt: str) -> bytes:
    """把数据序列化为指定格式的字节（用于 web 模式 src_bytes 下载）。"""
    df = exporter.to_dataframe(rows)
    buf = io.BytesIO()
    if fmt == "csv":
        buf.write(df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8"))
    elif fmt == "excel":
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
    elif fmt == "json":
        buf.write(df.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8"))
    else:
        raise ValueError(f"不支持的格式：{fmt}")
    return buf.getvalue()

AI_HINT = ("AI 模式：输入一句话即可，例如「爬这个页面的商品价格和标题，翻 3 页」"
           "「爬这个网站的所有分类」或「提取图片」。留空则用下方手动字段配置")


class MainView:
    def __init__(self, page: ft.Page):
        self.page = page
        self.last_rows = []          # 最近一次抓取结果（全部数据，分页显示用）
        self.last_fields = []        # 最近一次抓取的字段配置
        # 分页状态（数据预览区每次只渲染 PAGE_SIZE 行，避免 flet DataTable 渲染大量行卡死）
        self.page_size = 50
        self.current_page = 1
        self._build()
        self._restore_last_result()  # 恢复上次结果（web 刷新/重启后不丢数据）

    # ---------- 线程安全 UI 更新（关键） ----------
    # flet 的控件更新必须在主事件循环线程执行。后台抓取线程通过
    # _ui_update 把 UI 操作调度回主线程（page.run_task 内部用
    # asyncio.run_coroutine_threadsafe），否则控件变更不会被序列化
    # 发送到前端（数据预览不刷新），且与 WebSocket 主循环竞争导致
    # 连接中断（浏览器报错页）。
    def _ui_update(self, fn, *args, **kwargs):
        """线程安全地执行 UI 操作。

        fn: 要在主线程执行的函数（改控件 + page.update 都放里面）。
        """
        async def _apply():
            try:
                fn(*args, **kwargs)
                self.page.update()
            except Exception:
                pass  # 页面可能已销毁/刷新，忽略

        try:
            self.page.run_task(_apply)
        except Exception:
            # run_task 本身失败（如页面已关闭）：兜底直接调用
            try:
                fn(*args, **kwargs)
            except Exception:
                pass

    # ---------- 界面搭建 ----------
    def _build(self):
        p = self.page

        # 字段编辑器容器（动态添加行）
        self.field_list = ft.Column(spacing=6)
        self._add_field_row()  # 默认一行：标题

        self.url_input = ft.TextField(
            label="目标网址", hint_text="https://example.com",
            expand=True, dense=True,
        )
        self.fetcher_select = ft.Dropdown(
            label="抓取器", options=[
                ft.DropdownOption(key=k, text=t) for k, t in FETCHER_OPTIONS
            ], value="auto", width=130, dense=True,
        )

        # 引擎选择 + 浏览器窗口开关
        self.engine_select = ft.Dropdown(
            label="引擎", options=[
                ft.DropdownOption(key="auto", text="融合引擎（推荐）"),
                ft.DropdownOption(key="unified", text="融合引擎（手动）"),
                ft.DropdownOption(key="scrapling", text="selector 引擎"),
                ft.DropdownOption(key="direct", text="AI 直提引擎"),
                ft.DropdownOption(key="agent", text="AI 浏览器（自研）"),
                ft.DropdownOption(key="browser-use", text="browser-use（官方）"),
                ft.DropdownOption(key="crawl4ai", text="Crawl4AI（官方）"),
            ], value="auto", width=200, dense=True,
        )
        self.headless_checkbox = ft.Checkbox(
            label="显示浏览器窗口（可手动登录）", value=True)
        self.chrome_data_input = ft.TextField(
            label="浏览器数据目录（保存登录态；首次登录后自动恢复）",
            value=r"D:\workbuudy\Scrapling\.chrome-data",
            expand=True, dense=True, hint_text="保持默认即可",
        )

        # AI 指令输入框
        self.ai_input = ft.TextField(
            label="AI 指令（说人话就行）",
            hint_text="例如：爬这个页面的商品标题、价格和链接，翻 3 页",
            dense=True, expand=True,
        )

        # 手动模式翻页设置
        self.pg_count = ft.TextField(label="翻页数", value="1", width=70, dense=True,
                                     hint_text="1=不翻页")
        self.pg_start = ft.TextField(label="起始值", value="0", width=70, dense=True,
                                     hint_text="豆瓣0/页码式1")
        self.pg_template = ft.TextField(
            label="翻页 URL 模板（含 {page}，如 ?start={page}，步长 25）",
            hint_text="留空则不翻页", expand=True, dense=True,
        )
        self.pg_deep = ft.TextField(label="整站深爬页数", value="0", width=110,
                                    dense=True, hint_text="0=关, >1=BFS全站")

        self.add_field_btn = ft.ElevatedButton("＋ 添加字段", icon=ft.Icons.ADD,
                                               on_click=lambda e: self._add_field_row())
        self.start_btn = ft.ElevatedButton("▶ 开始抓取", icon=ft.Icons.PLAY_ARROW, on_click=self._on_start)
        self.progress = ft.ProgressRing(width=20, height=20, visible=False)
        self.status_text = ft.Text("就绪", size=12, color=ft.Colors.GREY_600)
        self.settings_btn = ft.IconButton(ft.Icons.SETTINGS, tooltip="设置（API Key）",
                                          on_click=self._open_settings)
        self.history_btn = ft.IconButton(ft.Icons.HISTORY, tooltip="抓取历史",
                                         on_click=self._open_history)

        # 导出按钮组
        self.export_btns = ft.Row(
            [ft.ElevatedButton("导出 CSV", on_click=lambda e: self._on_export(e, "csv")),
             ft.ElevatedButton("导出 Excel", on_click=lambda e: self._on_export(e, "excel")),
             ft.ElevatedButton("导出 JSON", on_click=lambda e: self._on_export(e, "json")),
             ft.ElevatedButton("下载图片", on_click=self._on_download_images)],
            spacing=8, visible=False,
        )

        # 数据预览表（分页：一次只渲染 PAGE_SIZE 行；全部数据在 self.last_rows）
        self.data_table = ft.DataTable(columns=[ft.DataColumn(ft.Text("暂无数据"))], rows=[], visible=False)
        # 「上次结果」提示条：本次抓取失败/没拿到新数据时，保留上次数据并提示
        self.stale_note = ft.Text(
            "（显示上次抓取结果，本次未获取到新数据）",
            size=12, color=ft.Colors.ORANGE_700, visible=False,
        )
        # 分页控件
        self.prev_btn = ft.ElevatedButton("‹ 上一页", on_click=lambda e: self._on_paginate(-1),
                                          disabled=True)
        self.next_btn = ft.ElevatedButton("下一页 ›", on_click=lambda e: self._on_paginate(1))
        self.page_label = ft.Text("", size=12, color=ft.Colors.GREY_600)
        self.pager = ft.Row([self.prev_btn, self.page_label, self.next_btn],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            visible=False)
        self.table_scroll = ft.Column(
            [self.stale_note, self.data_table, self.pager],
            scroll=ft.ScrollMode.AUTO,
            height=420,
        )

        # 运行日志（AI 过程可视化）
        self.log_area = ft.Column(scroll=ft.ScrollMode.AUTO, height=120,
                                  spacing=2, visible=False)

        # 导出文件选择器（flet 0.86：同步保存对话框）
        self.file_picker = ft.FilePicker()

        # 主布局
        p.add(
            ft.Row([ft.Text("智爬 · AI 网页数据采集", size=20, weight=ft.FontWeight.BOLD),
                    self.status_text, self.history_btn, self.settings_btn]),
            ft.Divider(),
            ft.Row([self.url_input, self.fetcher_select]),
            ft.Row([self.ai_input]),
            ft.Row([self.engine_select, self.headless_checkbox]),
            ft.Text(AI_HINT, size=12, color=ft.Colors.GREY_600),
            self.chrome_data_input,
            ft.Divider(),
            ft.Text("手动模式 · 要提取的字段（CSS 选择器，如 h1、.price、a.title）", size=12,
                    color=ft.Colors.GREY_600),
            self.field_list,
            ft.Row([self.add_field_btn, self.pg_count, self.pg_start,
                    self.pg_deep, self.pg_template]),
            ft.Divider(),
            ft.Row([self.start_btn, self.progress, self.export_btns]),
            ft.Divider(),
            ft.Row([ft.Text("运行日志", size=13, weight=ft.FontWeight.BOLD)]),
            self.log_area,
            ft.Divider(),
            ft.Text("数据预览", size=14, weight=ft.FontWeight.BOLD),
            self.table_scroll,
        )

    def _add_field_row(self, field: dict = None):
        """向字段编辑器添加一行；field 提供时预填值（用于历史重跑）。"""
        field = field or {}
        name_input = ft.TextField(hint_text="字段名", width=140, dense=True,
                                  value=field.get("name", ""))
        sel_input = ft.TextField(hint_text="CSS 选择器", expand=True, dense=True,
                                 value=field.get("selector", ""))
        type_select = ft.Dropdown(
            options=[ft.DropdownOption(key=k, text=t) for k, t in TYPE_OPTIONS],
            value=field.get("type", "text"), width=110, dense=True,
        )
        attr_input = ft.TextField(hint_text="属性名", width=110, dense=True,
                                  value=field.get("attr", ""),
                                  disabled=field.get("type", "text") != "attr")

        def on_type_change(e):
            attr_input.disabled = type_select.value != "attr"
            self.page.update()

        type_select.on_change = on_type_change

        del_btn = ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="删除该字段")
        row = ft.Row([name_input, sel_input, type_select, attr_input, del_btn], spacing=8)
        del_btn.on_click = lambda e, r=row: self._on_del_field(r)
        self.field_list.controls.append(row)
        self.page.update()

    def _on_del_field(self, row):
        self.field_list.controls.remove(row)
        self.page.update()

    def _on_paginate(self, delta: int):
        """翻页按钮回调：±1 切换当前页。"""
        self.current_page += delta
        if self.current_page < 1:
            self.current_page = 1
        if self.last_rows:
            self._ui_update(self._render_table, self.last_rows)

    # ---------- 上次结果恢复 ----------
    def _restore_last_result(self):
        """应用启动/页面刷新后恢复上次抓取结果，避免数据丢失。"""
        rows, fields, total = last_result.load_last_result()
        if not rows:
            return
        self.last_rows = rows
        self.last_fields = fields
        self.current_page = 1  # 恢复时重置到第一页
        # 不传 total_hint：让 _render_table 用实际行数（避免旧 last_result
        # 残留数字与新数据列数不一致时混乱）
        self._render_table(rows, total_hint=0)
        note = f"已恢复上次抓取结果（{len(rows)} 行"
        if total and total > len(rows):
            note += f"，持久化记录共 {total} 行"
        note += "）"
        self.stale_note.value = note
        self.stale_note.visible = True
        self.status_text.value = f"上次结果已恢复（共 {len(rows)} 行）"
        print(f"[last_result] 已恢复上次数据 {len(rows)} 行（total={total}）")
        self._show_snack(f"🔄 已恢复上次抓取结果（{len(rows)} 行）")
        self.page.update()

    # ---------- 历史记录 ----------
    def _save_history(self, ai_input, url, fetcher, pagination,
                      rows_count, used_fetcher):
        try:
            history.save_entry(history.build_entry(
                ai_input, url, fetcher, self.last_fields, pagination,
                rows_count, used_fetcher))
        except Exception:
            pass

    def _open_history(self, e):
        entries = history.load_history()
        if not entries:
            self._show_snack("暂无历史记录，先去抓一次吧")
            return
        items = []
        for idx, ent in enumerate(entries):
            title = ent.get("ai_input") or ent.get("url") or "（手动抓取）"
            sub = (f"{ent.get('time', '')} · {ent.get('rows_count', 0)} 行"
                   f" · {ent.get('used_fetcher', ent.get('fetcher', ''))}模式")
            items.append(ft.ListTile(
                title=ft.Text(str(title), size=13, max_lines=1),
                subtitle=ft.Text(sub, size=11),
                on_click=lambda e, i=idx: self._apply_history(i),
            ))
        dlg = ft.AlertDialog(
            title=ft.Text("抓取历史（点击载入配置）"),
            content=ft.Container(
                ft.Column(items, scroll=ft.ScrollMode.AUTO), width=560, height=380),
            actions=[ft.TextButton("关闭", on_click=lambda e: self.page.pop_dialog())],
        )
        self.page.show_dialog(dlg)

    def _apply_history(self, idx):
        entries = history.load_history()
        if idx >= len(entries):
            return
        ent = entries[idx]
        self.url_input.value = ent.get("url", "")
        self.ai_input.value = ent.get("ai_input", "")
        self.fetcher_select.value = ent.get("fetcher", "auto")
        # 重建字段
        self.field_list.controls.clear()
        for f in ent.get("fields", []):
            self._add_field_row(f)
        # 翻页/深爬
        pag = ent.get("pagination") or {}
        if pag.get("mode") == "deep":
            self.pg_deep.value = str(pag.get("max_pages", 0))
            self.pg_count.value = "1"
            self.pg_start.value = "0"
            self.pg_template.value = ""
        elif pag.get("mode") == "url_pattern":
            self.pg_deep.value = "0"
            self.pg_count.value = str(pag.get("max_pages", 1))
            self.pg_start.value = str(pag.get("start", 0))
            self.pg_template.value = pag.get("url_pattern", "")
        else:
            self.pg_deep.value = "0"
            self.pg_count.value = "1"
            self.pg_start.value = "0"
            self.pg_template.value = ""
        self.page.pop_dialog()
        self._show_snack("已载入历史配置，点「开始抓取」重跑")

    # ---------- 设置 ----------
    def _open_settings(self, e):
        cfg = load_config()
        self.api_key_input = ft.TextField(
            label="DeepSeek API Key（在 platform.deepseek.com 获取）",
            value=cfg.get("api_key", ""), password=True, width=420,
        )
        dlg = ft.AlertDialog(
            title=ft.Text("设置"),
            content=self.api_key_input,
            actions=[
                ft.TextButton("保存", on_click=self._save_settings),
                ft.TextButton("取消", on_click=lambda e: self.page.pop_dialog()),
            ],
        )
        self.page.show_dialog(dlg)

    def _save_settings(self, e):
        cfg = load_config()
        cfg["api_key"] = self.api_key_input.value.strip()
        save_config(cfg)
        self.page.pop_dialog()
        self._show_snack("API Key 已保存" if cfg["api_key"] else "已清空 API Key")

    # ---------- 抓取 ----------
    def _collect_fields(self):
        """从界面读取字段配置。"""
        fields = []
        for row in self.field_list.controls:
            name, sel, typ, attr = row.controls[:4]
            if not (name.value or sel.value):
                continue
            fields.append({
                "name": name.value or "字段",
                "selector": sel.value,
                "type": typ.value,
                "attr": attr.value or "",
            })
        return fields

    def _collect_pagination(self):
        """读取手动模式翻页/深爬设置。"""
        # 整站深爬优先
        try:
            deep_pages = max(0, int((self.pg_deep.value or "0").strip()))
        except ValueError:
            deep_pages = 0
        if deep_pages > 1:
            return {"mode": "deep", "max_pages": deep_pages}
        try:
            pages = max(1, int((self.pg_count.value or "1").strip()))
        except ValueError:
            pages = 1
        try:
            start = max(0, int((self.pg_start.value or "0").strip()))
        except ValueError:
            start = 0
        template = self.pg_template.value.strip()
        if not template:
            return None
        return {"mode": "url_pattern", "url_pattern": template,
                "max_pages": pages, "step": 25, "start": start}

    def _on_start(self, e):
        if self.start_btn.disabled:
            return
        self.start_btn.disabled = True
        self.progress.visible = True
        self.export_btns.visible = False
        # 不再隐藏数据表：保留上次结果直到新数据到来（避免抓取中/失败时数据消失）
        self.stale_note.visible = False
        self.log_area.controls.clear()
        if self.last_rows:
            self.status_text.value = f"正在抓取新数据…（下方表格仍显示上次的 {len(self.last_rows)} 行结果）"
        else:
            self.status_text.value = "准备中…"
        self.page.update()

        url = self.url_input.value.strip()
        fetcher = self.fetcher_select.value
        fields = self._collect_fields()
        ai_input = self.ai_input.value.strip()
        pagination = self._collect_pagination()

        threading.Thread(target=self._scrape_worker,
                         args=(url, fetcher, fields, ai_input, pagination),
                         daemon=True).start()

    def _scrape_worker(self, url, fetcher, fields, ai_input, pagination):
        """后台线程执行抓取（AI 或手动模式）。

        关键：抓取本身在后台线程跑，但**所有 flet 控件操作必须调度回
        主线程**（_ui_update），否则数据预览不刷新、WebSocket 竞争断连。
        """
        def _set_finished_state(msg, show_table, rows=None, stale=False,
                                stale_text=""):
            """在主线程统一收尾：状态栏 + 表格 + 提示条 + 按钮恢复。"""
            self.status_text.value = msg
            if show_table and rows:
                self._render_table(rows)
            self.stale_note.visible = stale
            if stale:
                self.stale_note.value = stale_text
            self.start_btn.disabled = False
            self.progress.visible = False
            self.export_btns.visible = bool(self.last_rows)

        try:
            if ai_input:
                self._add_log("▶ 开始 AI 抓取流程…")
                engine = self.engine_select.value or "auto"
                headless = not self.headless_checkbox.value
                user_data_dir = (self.chrome_data_input.value or "").strip()
                # 深爬参数（Crawl4AI 引擎用）：整站深爬页数 → 深度
                deep_pages = 0
                try:
                    deep_pages = max(0, int((self.pg_deep.value or "0").strip()))
                except ValueError:
                    deep_pages = 0
                kwargs = {}
                if deep_pages > 0:
                    kwargs["deep_max_depth"] = min(deep_pages, 5)
                    kwargs["max_pages"] = deep_pages * 20
                rows, config, attempts, used = run_ai_scrape(
                    ai_input, url, progress=self._add_log,
                    engine=engine, headless=headless,
                    user_data_dir=user_data_dir, **kwargs)
                self.last_fields = config.get("fields", [])
                used_fetcher = used
                msg = f"AI 完成（{used}模式，尝试 {attempts} 次，{len(rows)} 行）"
            elif fetcher == "auto":
                rows, status, used = auto_fetch(url, fields, pagination=pagination)
                self.last_fields = fields
                used_fetcher = used
                msg = f"完成（{used}模式，HTTP {status}，{len(rows)} 行）"
            else:
                rows, status = scrape(url, fetcher, fields, pagination=pagination)
                self.last_fields = fields
                used_fetcher = fetcher
                msg = f"完成（HTTP {status}，{len(rows)} 行）"
            # 成功但 0 行：不覆盖上次数据（保留旧结果，避免"刷新/重跑后白抓"）
            if rows:
                self.last_rows = rows
                last_result.save_last_result(rows, fields)
                self._ui_update(_set_finished_state, msg, True, rows)
            elif self.last_rows:
                self._ui_update(_set_finished_state,
                                msg + "（本次 0 行，保留上次数据）", True,
                                self.last_rows, True,
                                "（本次抓取返回 0 行，保留上次结果）")
            else:
                self._ui_update(_set_finished_state, msg, False)
            self._save_history(ai_input, url, fetcher, pagination,
                               len(rows), used_fetcher)
        except ScrapeError as err:
            # 失败不丢数据：保留上次结果继续展示（仅提示），除非从未抓到过
            if self.last_rows:
                self._ui_update(_set_finished_state,
                                f"本次抓取失败：{err}（下方仍显示上次的 "
                                f"{len(self.last_rows)} 行结果）", True,
                                self.last_rows, True,
                                "（本次抓取失败，保留上次结果）")
                self._show_snack(f"抓取失败：{err}（已保留上次数据）")
            else:
                self._ui_update(_set_finished_state, f"失败：{err}", False)
                self._show_snack(f"抓取失败：{err}")
        except Exception as err:
            if self.last_rows:
                self._ui_update(_set_finished_state,
                                f"本次出错：{err}（下方仍显示上次的 "
                                f"{len(self.last_rows)} 行结果）", True,
                                self.last_rows, True,
                                "（本次出错，保留上次结果）")
                self._show_snack(f"发生错误：{err}（已保留上次数据）")
            else:
                self._ui_update(_set_finished_state, f"出错：{err}", False)
                self._show_snack(f"发生错误：{err}")
        finally:
            # 兜底：确保按钮/进度条恢复（若上面未走 _ui_update 分支）
            def _ensure_finished():
                self.start_btn.disabled = False
                self.progress.visible = False
                self.export_btns.visible = bool(self.last_rows)
            self._ui_update(_ensure_finished)

    def _render_table(self, rows, total_hint: int = 0):
        """把抓取结果渲染成 DataTable（分页：一次 PAGE_SIZE 行）。URL 字段渲染为可点击链接。

        关键：①必须**先清空 rows 再设 columns 再设 rows**，避免新旧列数不一致
        触发 flet "DataRow/DataCells 列数不匹配" 校验报错；②用**实际 rows 数**（不
        用 total_hint）避免旧 last_result 残留数字干扰显示；③单行渲染加
        try/except 防御，个别坏行不传染；④**分页**渲染（flet DataTable 一次渲染
        几百行会卡死），self.last_rows 保留全部数据，self.current_page 控制当前页。
        """
        if not rows:
            self.data_table.rows = []
            self.data_table.columns = [ft.DataColumn(ft.Text("暂无数据"))]
            self.data_table.visible = False
            self.pager.visible = False
            return
        cols = list(rows[0].keys())
        # 1. 清空旧 rows（rows=0 永不触发"cell 数 != 列数"校验）
        self.data_table.rows = []
        # 2. 设新 columns
        self.data_table.columns = [ft.DataColumn(ft.Text(c, weight=ft.FontWeight.BOLD))
                                  for c in cols]
        # 3. 分页计算 + 渲染当前页
        total = len(rows)
        total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        # current_page 越界保护（重新设置后页数可能减少）
        if self.current_page > total_pages:
            self.current_page = total_pages
        if self.current_page < 1:
            self.current_page = 1
        start_idx = (self.current_page - 1) * self.page_size
        end_idx = start_idx + self.page_size
        page_rows = rows[start_idx:end_idx]
        # 4. 渲染当前页（单行 try/except 防御坏行）
        cells = []
        for r in page_rows:
            row_cells = []
            for c in cols:
                try:
                    v = str(r.get(c, ""))
                    row_cells.append(ft.DataCell(self._render_cell(c, v)))
                except Exception:
                    # 单行坏掉不传染：填占位 cell
                    row_cells.append(ft.DataCell(ft.Text("(渲染错误)",
                                                          size=11)))
            cells.append(ft.DataRow(cells=row_cells))
        # 5. 同步设 rows + 翻页控件
        self.data_table.rows = cells
        self.data_table.visible = True
        self.prev_btn.disabled = (self.current_page <= 1)
        self.next_btn.disabled = (self.current_page >= total_pages)
        self.page_label.value = f"第 {self.current_page}/{total_pages} 页 · 共 {total} 条"
        self.pager.visible = True

    def _render_cell(self, field_name: str, value: str):
        """单元格渲染：URL/链接→可点击超链接；图片→可点击缩略图；文本自动换行。

        - 链接字段（链接/url/网址/href）或值以 http 开头 → 可点击链接
        - 图片字段（图片/封面/缩略图/img/cover）或值以 http+图片后缀 → 缩略图
        - 普通文本 → 自动换行
        """
        if not value:
            return ft.Container(content=ft.Text("-", size=12), width=80)
        v = str(value).strip()
        lower = v.lower()
        fname = (field_name or "").lower()
        is_img_field = any(k in fname for k in ("图片", "封面", "缩略图",
                                                "img", "image", "cover",
                                                "海报", "src"))
        is_url_field = any(k in fname for k in ("链接", "url", "网址",
                                                "href", "address"))
        looks_img = lower.startswith(("http://", "https://")) and any(
            lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif",
                                            ".webp", ".bmp", ".svg"))
        looks_url = lower.startswith(("http://", "https://"))

        # 图片字段且值像图片 URL → 可点击缩略图
        if (is_img_field and (looks_url or lower.startswith("/"))) or looks_img:
            display = v if len(v) <= 40 else v[:40] + "…"
            try:
                return ft.Container(
                    content=ft.Link(
                        content=ft.Image(
                            src=v if looks_url else "https:" + v,
                            width=60, height=80,
                            fit=ft.ImageFit.COVER,
                            border_radius=4,
                        ),
                        url=v, tooltip=v,
                    ),
                    padding=2,
                )
            except Exception:
                # 图片渲染失败退化为链接文本
                return ft.Container(
                    content=ft.Link(
                        content=ft.Text(display, size=12,
                                        color=ft.Colors.BLUE),
                        url=v, tooltip=v),
                    padding=4, width=120,
                )
        # URL/链接字段（或值像链接）→ 可点击超链接
        if is_url_field or looks_url:
            display = v if len(v) <= 50 else v[:50] + "…"
            return ft.Container(
                content=ft.Link(
                    content=ft.Text(display, size=12,
                                    color=ft.Colors.BLUE,
                                    weight=ft.FontWeight.W_500),
                    url=v, tooltip=v),
                padding=4, width=200,
            )
        # 普通文本：宽 180 + 自动换行（长文本占满列宽不挤压其他列）
        return ft.Container(
            content=ft.Text(value, size=12, max_lines=3,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            no_wrap=False),
            padding=4, width=180,
        )

    # ---------- 图片下载 ----------
    def _on_download_images(self, e):
        if not self.last_rows:
            return
        urls = exporter.collect_image_urls(self.last_rows, self.last_fields)
        if not urls:
            self._show_snack("没有找到图片字段（请确认字段类型选了「图片」）")
            return
        self.page.run_task(self._do_download_images, urls)

    async def _do_download_images(self, urls):
        try:
            save_dir = await self.file_picker.get_directory_path()
        except Exception as err:
            self._show_snack(f"选择文件夹失败：{err}")
            return
        if not save_dir:
            return
        ok, fail = exporter.download_images(urls, save_dir)
        self._show_snack(f"图片下载完成：成功 {ok} 张，失败 {fail} 张 → {save_dir}")

    # ---------- 导出 ----------
    def _on_export(self, e, fmt):
        if not self.last_rows:
            return
        # FilePicker.save_file 是 async 方法（web 模式下还必须传 src_bytes）
        self.page.run_task(self._do_export, fmt)

    async def _do_export(self, fmt):
        try:
            data = _build_export_bytes(self.last_rows, fmt)
        except Exception as err:
            self._show_snack(f"生成文件失败：{err}")
            return
        fname, exts, _ = EXPORT_FORMATS[fmt]
        try:
            path = await self.file_picker.save_file(
                file_name=fname, allowed_extensions=exts, src_bytes=data)
        except Exception as err:
            self._show_snack(f"打开保存对话框失败：{err}")
            return
        if not path:
            return
        self._show_snack(f"已保存：{Path(path).name}")

    def _add_log(self, msg):
        """追加一条运行日志（AI 过程可视化，最多保留 50 条）。

        由后台抓取线程调用 → 必须走线程安全更新，否则 UI 不刷新。
        """
        def _apply():
            self.log_area.controls.append(ft.Text(
                f"· {msg}", size=12, color=ft.Colors.GREY_800))
            if len(self.log_area.controls) > 50:
                del self.log_area.controls[:-50]
            self.log_area.visible = True
            self.status_text.value = msg
        self._ui_update(_apply)

    def _show_snack(self, msg):
        """显示提示条（线程安全；自动清理旧 snack 避免 overlay 堆积）。"""
        def _apply():
            # 先移除旧的 SnackBar（否则 overlay 无限累积，页面变卡）
            self.page.overlay[:] = [
                o for o in self.page.overlay
                if not isinstance(o, ft.SnackBar)
            ]
            snack = ft.SnackBar(content=ft.Text(msg))
            snack.open = True
            self.page.overlay.append(snack)
        self._ui_update(_apply)
