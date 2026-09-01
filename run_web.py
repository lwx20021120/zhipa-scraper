# -*- coding: utf-8 -*-
"""Web 模式启动入口：自动打开浏览器使用智爬。

双击「启动智爬.bat」即可；或手动运行：
    D:\\python\\python.exe run_web.py
"""
import traceback
import flet as ft

from app.main import main

if __name__ == "__main__":
    try:
        # WEB_BROWSER：启动本地服务并自动打开默认浏览器
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=8550)
    except Exception:
        traceback.print_exc()
        input("程序异常退出，按回车关闭窗口...")
