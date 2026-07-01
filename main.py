# -*- coding: utf-8 -*-
"""
astrbot_plugin_economy - 棱镜娘经济系统

对齐类脑娘 Odyssey Coin：
- /余额  查询棱镜币余额
- /商店  浏览商品（分类展示）
- /购买  购买商店道具
- 每日首次发言奖励
"""

import os
import random
import sqlite3
from datetime import datetime, timezone, timedelta

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

BEIJING_TZ = timezone(timedelta(hours=8))
CURRENCY = "棱镜币"
CURRENCY_EMOJI = "💎"

# 预设商店商品（对齐类脑娘商店分类）
SHOP_ITEMS = [
    # 送给棱镜娘的礼物 → 联动好感度
    {"id": 1, "name": "草莓蛋糕", "desc": "一块精致的草莓蛋糕，棱镜娘最喜欢的甜品", "price": 50, "category": "礼物", "effect": "gift"},
    {"id": 2, "name": "玫瑰奶茶",  "desc": "温暖的玫瑰奶茶，让她在忙碌中也能感受到你的心意",   "price": 30, "category": "礼物", "effect": "gift"},
    {"id": 3, "name": "樱花布丁",  "desc": "春天限定的樱花布丁，粉嫩嫩的超可爱",             "price": 40, "category": "礼物", "effect": "gift"},
    {"id": 4, "name": "巧克力礼盒","desc": "精选手工巧克力，每一颗都是爱的形状",             "price": 80, "category": "礼物", "effect": "gift"},
    {"id": 5, "name": "豪华寿司船","desc": "一整船的豪华寿司，棱镜娘看了眼睛都直了",         "price": 150,"category": "礼物", "effect": "gift"},
    # 功能性道具
    {"id": 6, "name": "记忆水晶",   "desc": "解锁棱镜娘对你的个人记忆功能",                   "price": 200,"category": "道具", "effect": "unlock_memory"},
    {"id": 7, "name": "遗忘药水",   "desc": "让棱镜娘忘记关于你的记忆，重新开始",             "price": 300,"category": "道具", "effect": "clear_memory"},
    {"id": 8, "name": "幸运符",     "desc": "据说能带来好运的神秘符咒（效果随机）",           "price": 100,"category": "道具", "effect": "random"},
]


class EconomyDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS coins (
                    user_id TEXT PRIMARY KEY,
                    balance INTEGER DEFAULT 0,
                    last_daily_date TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inventory (
                    user_id TEXT,
                    item_id INTEGER,
                    quantity INTEGER DEFAULT 1,
                    PRIMARY KEY (user_id, item_id)
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    amount INTEGER,
                    reason TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
            """)
            conn.commit()

    def get_balance(self, user_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT balance FROM coins WHERE user_id = ?", (user_id,)).fetchone()
            return row["balance"] if row else 0

    def add_coins(self, user_id: str, amount: int, reason: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("SELECT balance FROM coins WHERE user_id = ?", (user_id,)).fetchone()
            new_bal = (cur["balance"] if cur else 0) + amount
            conn.execute(
                "INSERT INTO coins (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = ?",
                (user_id, new_bal, new_bal),
            )
            conn.execute("INSERT INTO transactions (user_id, amount, reason) VALUES (?, ?, ?)", (user_id, amount, reason))
            conn.commit()
        return new_bal

    def remove_coins(self, user_id: str, amount: int, reason: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT balance FROM coins WHERE user_id = ?", (user_id,)).fetchone()
            if not row or row["balance"] < amount:
                return None
            new_bal = row["balance"] - amount
            conn.execute("UPDATE coins SET balance = ? WHERE user_id = ?", (new_bal, user_id))
            conn.execute("INSERT INTO transactions (user_id, amount, reason) VALUES (?, ?, ?)", (user_id, -amount, reason))
            conn.commit()
        return new_bal

    def daily_reward(self, user_id: str) -> int | None:
        today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute("SELECT last_daily_date FROM coins WHERE user_id = ?", (user_id,)).fetchone()
            if row and row["last_daily_date"] == today:
                return None
            reward = 10
            cur = conn.execute("SELECT balance FROM coins WHERE user_id = ?", (user_id,)).fetchone()
            new_bal = (cur["balance"] if cur else 0) + reward
            conn.execute(
                "INSERT INTO coins (user_id, balance, last_daily_date) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = ?, last_daily_date = ?",
                (user_id, new_bal, today, new_bal, today),
            )
            conn.execute("INSERT INTO transactions (user_id, amount, reason) VALUES (?, ?, ?)", (user_id, reward, "每日首次发言奖励"))
            conn.commit()
        return reward

    def add_to_inventory(self, user_id: str, item_id: int):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO inventory (user_id, item_id) VALUES (?, ?) ON CONFLICT(user_id, item_id) DO UPDATE SET quantity = quantity + 1",
                (user_id, item_id),
            )
            conn.commit()

    def get_inventory(self, user_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute("SELECT item_id, quantity FROM inventory WHERE user_id = ?", (user_id,)).fetchall()
            return [dict(r) for r in rows]


def _find_item(query: str) -> dict | None:
    q = query.strip().lower()
    for item in SHOP_ITEMS:
        if str(item["id"]) == q or item["name"].lower() == q:
            return item
    # fuzzy match
    for item in SHOP_ITEMS:
        if q in item["name"].lower():
            return item
    return None


class EconomyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        self.db = EconomyDB(os.path.join(data_dir, "economy.db"))
        self.db.init()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_daily_reward(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        if not uid:
            return
        reward = self.db.daily_reward(uid)
        if reward:
            logger.info(f"[Economy] 用户 {uid} 获得每日奖励 {reward} {CURRENCY}")

    @filter.command("余额")
    async def cmd_balance(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        bal = self.db.get_balance(uid)
        yield event.plain_result(f"{CURRENCY_EMOJI} 你的{CURRENCY}余额: **{bal}**")

    @filter.command("商店")
    async def cmd_shop(self, event: AstrMessageEvent):
        categories = {}
        for item in SHOP_ITEMS:
            cat = item["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        lines = [f" {CURRENCY_EMOJI} 棱镜娘商店\n"]
        for cat, items in categories.items():
            lines.append(f"【{cat}】")
            for item in items:
                lines.append(f"  [{item['id']}] {item['name']} — {item['price']}{CURRENCY}")
                lines.append(f"      {item['desc']}")
            lines.append("")
        lines.append("使用 /购买 <编号或名称> 来购买")
        yield event.plain_result("\n".join(lines))

    @filter.command("购买")
    async def cmd_buy(self, event: AstrMessageEvent, item_query: str = ""):
        uid = event.get_sender_id()
        if not item_query.strip():
            yield event.plain_result("想买什么？用 /商店 看看有什么好东西吧～")
            return

        item = _find_item(item_query)
        if not item:
            yield event.plain_result(f"没找到「{item_query}」呢，用 /商店 看看有哪些商品吧～")
            return

        bal = self.db.get_balance(uid)
        if bal < item["price"]:
            yield event.plain_result(
                f"{CURRENCY_EMOJI} 余额不足！\n需要 {item['price']}{CURRENCY}，你只有 {bal}{CURRENCY}"
            )
            return

        new_bal = self.db.remove_coins(uid, item["price"], f"购买 {item['name']}")
        if new_bal is None:
            yield event.plain_result("购买失败，请稍后再试。")
            return

        self.db.add_to_inventory(uid, item["id"])

        effect_msg = ""
        if item["effect"] == "gift":
            effect_msg = f"\n  {item['name']}会自动增加棱镜娘对你的好感度哦～"
        elif item["effect"] == "unlock_memory":
            effect_msg = "\n  记忆水晶已激活！棱镜娘会开始记住关于你的事。"
        elif item["effect"] == "clear_memory":
            effect_msg = "\n  遗忘药水已生效，棱镜娘关于你的记忆被清除了。"
        elif item["effect"] == "random":
            r = random.randint(1, 100)
            effect_msg = f"\n  幸运符闪烁了一下...（效果值: {r}）"

        yield event.plain_result(
            f"  购买成功！\n你购买了 **{item['name']}**，花费 {item['price']}{CURRENCY}\n"
            f"当前余额: {new_bal}{CURRENCY}{effect_msg}"
        )

    async def terminate(self):
        logger.info("[Economy] 插件已卸载")
