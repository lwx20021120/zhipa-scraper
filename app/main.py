# -*- coding: utf-8 -*-
"""智爬（Scrapling Desktop）程序入口。

运行方式（在项目根目录 D:\\workbuudy\\Scrapling 下）：
    D:\\python\\python.exe -m app.main
"""
import flet as ft

from app.ui.main_view import MainView


def main(page: ft.Page):
    page.title = "智爬 · AI 网页数据采集"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window.width = 1100
    page.window.height = 780
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO  # 内容超出窗口时整页可滚动
    MainView(page)


if __name__ == "__main__":
    ft.run(main)
