# -*- coding: utf-8 -*-
"""排查 _run_async 的异步嵌套 loop 问题：模拟两种调用场景。"""
import sys
import threading
import traceback

sys.path.insert(0, r"D:\workbuudy\Scrapling")
from app.engines import _run_async


def make_coro_factory():
    """一个简单的协程工厂：模拟 BrowserUseEngine._run_agent。"""
    async def _inner():
        import asyncio
        await asyncio.sleep(0.05)
        return "OK-from-coro"
    def factory():
        return _inner()
    return factory


def test_background_thread():
    """场景A：后台工作线程（无运行中 loop）→ 走 asyncio.run 路径。"""
    result = {}
    def worker():
        try:
            r = _run_async(make_coro_factory())
            result["ok"] = r
        except Exception as e:
            result["err"] = f"{type(e).__name__}: {e}"
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)
    print("[场景A] 后台线程无loop →", result)
    return "err" not in result


def test_running_loop_thread():
    """场景B：已有运行中 loop 的线程（UI 主线程）→ 走 nest_asyncio 路径。"""
    import asyncio
    import nest_asyncio
    result = {}

    async def outer():
        try:
            r = _run_async(make_coro_factory())
            result["ok"] = r
        except Exception as e:
            result["err"] = f"{type(e).__name__}: {e}"

    # 模拟 flet UI 线程：先有一个 loop 在跑
    nest_asyncio.apply()
    asyncio.run(outer())
    print("[场景B] 已有loop内嵌套调用 →", result)
    return "err" not in result


def test_nested_agent_like():
    """场景C：模拟 browser-use 内部结构 —— 协程内再创建子任务（Agent.run 结构）。"""
    import asyncio

    async def _sub():
        await asyncio.sleep(0.02)
        return "sub-ok"

    async def _run_agent_like():
        # 模拟 BrowserUseEngine._run_agent：里面 await 多个东西
        r1 = await _sub()
        # 模拟 browser-use 内部 asyncio.create_task（Agent.run 内部常用）
        task = asyncio.create_task(_sub())
        r2 = await task
        return f"{r1}+{r2}"

    result = {}
    def worker():
        try:
            r = _run_async(lambda: _run_agent_like())
            result["ok"] = r
        except Exception as e:
            result["err"] = f"{type(e).__name__}: {e}"
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)
    print("[场景C] agent 内部 create_task 结构 →", result)
    return "err" not in result


if __name__ == "__main__":
    ok_a = test_background_thread()
    ok_b = test_running_loop_thread()
    ok_c = test_nested_agent_like()
    print("\n结论:", "全部通过" if (ok_a and ok_b and ok_c) else "存在问题")
    sys.exit(0 if (ok_a and ok_b and ok_c) else 1)
