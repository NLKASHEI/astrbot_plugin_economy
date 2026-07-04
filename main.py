# -*- coding: utf-8 -*-
"""
astrbot_plugin_economy - 棱镜娘经济系统 v1.2

对齐类脑娘 Odyssey Coin：
- /balance  查询货币余额（人格化展示）
- /shop  浏览商品（分类+emoji+详细描述）
- /buy  购买道具（好感度联动）
- /transactions  查看最近交易
- 每日首次发言奖励（可配置浮动）
- 好感度联动：购买礼物自动增加好感度
- WebUI 可配置货币名称/图标/每日奖励范围
"""

import os
import random
import sqlite3
from datetime import datetime, timezone, timedelta

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig

BEIJING_TZ = timezone(timedelta(hours=8))

# 预设商店商品
SHOP_ITEMS = [
    # 送给 Bot 的礼物 → 联动好感度
    {"id": 1, "name": "草莓蛋糕",  "emoji": "🍰",
     "desc": "一块精致的草莓蛋糕，Bot最喜欢的甜品，甜而不腻的奶油配上新鲜草莓",
     "price": 50, "category": "🎁 礼物", "effect": "gift", "affection_bonus": 10},
    {"id": 2, "name": "玫瑰奶茶",  "emoji": "🧋",
     "desc": "温暖的玫瑰奶茶，玫瑰花瓣的香气让人在忙碌中也能感受到你的心意",
     "price": 30, "category": "🎁 礼物", "effect": "gift", "affection_bonus": 6},
    {"id": 3, "name": "樱花布丁",  "emoji": "🍮",
     "desc": "春天限定的樱花布丁，粉嫩嫩的超可爱，入口即化",
     "price": 40, "category": "🎁 礼物", "effect": "gift", "affection_bonus": 8},
    {"id": 4, "name": "巧克力礼盒","emoji": "🍫",
     "desc": "精选手工巧克力礼盒，每一颗都是爱的形状，丝滑浓郁",
     "price": 80, "category": "🎁 礼物", "effect": "gift", "affection_bonus": 15},
    {"id": 5, "name": "豪华寿司船","emoji": "🍣",
     "desc": "一整船的豪华寿司盛宴！三文鱼、金枪鱼、甜虾...Bot看了眼睛都直了",
     "price": 150,"category": "🎁 礼物", "effect": "gift", "affection_bonus": 25},
    # 功能性道具
    {"id": 6, "name": "记忆水晶",  "emoji": "💎",
     "desc": "闪耀的记忆水晶，解锁Bot对你的个人记忆功能，让她开始记住关于你的事",
     "price": 200,"category": "🔧 道具", "effect": "unlock_memory", "affection_bonus": 0},
    {"id": 7, "name": "遗忘药水",  "emoji": "🧪",
     "desc": "神秘的紫色药水，让Bot忘记关于你的所有记忆，一切重新开始",
     "price": 300,"category": "🔧 道具", "effect": "clear_memory", "affection_bonus": 0},
    {"id": 8, "name": "幸运符",    "emoji": "🍀",
     "desc": "据说能带来好运的神秘符咒，效果随机，可能是惊喜也可能是惊吓哦",
     "price": 100,"category": "🔧 道具", "effect": "random", "affection_bonus": 0},
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

    def daily_reward(self, user_id: str, min_r: int, max_r: int) -> int | None:
        today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute("SELECT last_daily_date FROM coins WHERE user_id = ?", (user_id,)).fetchone()
            if row and row["last_daily_date"] == today:
                return None
            reward = random.randint(min_r, max_r)
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

    def get_recent_transactions(self, user_id: str, limit: int = 10) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT amount, reason, created_at FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]


def _find_item(query: str) -> dict | None:
    q = query.strip().lower()
    for item in SHOP_ITEMS:
        if str(item["id"]) == q or item["name"].lower() == q:
            return item
    for item in SHOP_ITEMS:
        if q in item["name"].lower():
            return item
    return None


class EconomyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        self.db = EconomyDB(os.path.join(data_dir, "economy.db"))
        self.db.init()

        cfg = config or {}
        self.currency_name = str(cfg.get("currency_name", "棱镜币"))
        self.currency_emoji = str(cfg.get("currency_emoji", "💎"))
        self.daily_reward_min = int(cfg.get("daily_reward_min", 5))
        self.daily_reward_max = int(cfg.get("daily_reward_max", 20))
        self.transaction_limit = int(cfg.get("transaction_limit", 10))

        # 好感度跨插件 DB
        self._affection_db_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "astrbot_plugin_affection", "data", "affection.db"
        )

    # ==================== 人格读取 ====================

    async def _get_persona(self, event: AstrMessageEvent) -> tuple[str, str]:
        try:
            pm = self.context.persona_manager
            persona = await pm.get_default_persona_v3(umo=event.unified_msg_origin)
            if persona:
                return "棱镜娘", persona.get("prompt", "")
        except Exception:
            pass
        return "棱镜娘", ""

    async def _send_ephemeral(self, event, content):
        """Discord 斜杠命令私密回复；返回 True 表示已发送，False 需调用方 yield"""
        wh = getattr(event, 'interaction_followup_webhook', None)
        if wh:
            await wh.send(content, ephemeral=True)
            return True
        return False

    # ==================== 被动每日奖励 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_daily_reward(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        if not uid or uid == event.get_self_id():
            return  # 忽略 Bot 自己的消息
        reward = self.db.daily_reward(uid, self.daily_reward_min, self.daily_reward_max)
        if reward:
            logger.info(f"[Economy] 用户 {uid} 获得每日奖励 {reward} {self.currency_name}")

    # ==================== 记忆注入 ====================

    @filter.on_llm_request()
    async def inject_economy_context(self, event: AstrMessageEvent, req):
        """将用户余额注入 AI 上下文，让 Bot 记住用户的经济状况"""
        uid = event.get_sender_id()
        if not uid:
            return
        bal = self.db.get_balance(uid)
        if hasattr(req, "system_prompt"):
            req.system_prompt += (
                f"\n[经济系统] 当前用户的{self.currency_name}余额: {bal}\n"
            )

    # ==================== /余额 ====================

    @filter.command("balance", alias={"余额"})
    async def cmd_balance(self, event: AstrMessageEvent):
        """查询你的棱镜币余额"""
        uid = event.get_sender_id()
        uname = event.get_sender_name() or "你"
        bal = self.db.get_balance(uid)

        # 根据余额给出人格化评价
        if bal <= 0:
            mood = "嗯...目前手头有点紧呢。多聊聊天、打打工，攒点钱吧～"
        elif bal < 50:
            mood = "有一点点积蓄了呢，继续加油哦！"
        elif bal < 200:
            mood = "不错嘛，已经可以买点小礼物了～"
        elif bal < 500:
            mood = "哇，小有积蓄了！想买什么好东西吗？"
        elif bal < 1000:
            mood = "是个小富豪了呢！"
        else:
            mood = "天哪，你就是传说中的大富翁？！"

        lines = [
            f"{self.currency_emoji} **{uname} 的{self.currency_name}账户**",
            "",
            f"余额: **{bal}** {self.currency_name}",
            f"*{mood}*",
        ]
        content = "\n".join(lines)
        if not await self._send_ephemeral(event, content):
            yield event.plain_result(content)

    # ==================== /商店 ====================

    @filter.command("shop", alias={"商店"})
    async def cmd_shop(self, event: AstrMessageEvent):
        """查看棱镜娘商店——用棱镜币买礼物、道具"""
        uid = event.get_sender_id()
        bal = self.db.get_balance(uid)
        bot_name, _ = await self._get_persona(event)

        categories = {}
        for item in SHOP_ITEMS:
            cat = item["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        lines = [
            f"# {self.currency_emoji} {bot_name}的商店",
            f"*你的余额: {bal} {self.currency_name}*",
            "",
        ]

        for cat, items in categories.items():
            lines.append(f"## {cat}")
            for item in items:
                price_tag = f"{item['price']}{self.currency_name}"
                lines.append(f"**[{item['id']}] {item['emoji']} {item['name']}** — {price_tag}")
                lines.append(f"  > {item['desc']}")
                if item.get("affection_bonus", 0) > 0:
                    lines.append(f"  💕 好感度 +{item['affection_bonus']}")
            lines.append("")

        lines.append(f"使用 `/buy <编号或名称>` 或点击下方按钮购买")
        content = "\n".join(lines)
        # Discord 斜杠命令：发送带按钮的面板
        wh = getattr(event, 'interaction_followup_webhook', None)
        if wh:
            import discord as _discord
            view = _discord.ui.View(timeout=300)
            for item in SHOP_ITEMS:
                view.add_item(_discord.ui.Button(
                    label=f"买 {item['emoji']} {item['name']}",
                    custom_id=f"economy_buy_{item['id']}",
                    style=_discord.ButtonStyle.primary,
                ))
            await wh.send(content, view=view, ephemeral=True)
            return
        if not await self._send_ephemeral(event, content):
            yield event.plain_result(content)

    # ==================== 按钮交互处理 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_button_click(self, event: AstrMessageEvent):
        """处理商店购买按钮点击"""
        if not hasattr(event, 'message_obj') or not event.message_obj:
            return
        raw = getattr(event.message_obj, 'raw_message', None)
        if raw is None:
            return
        import discord as _discord
        if not isinstance(raw, _discord.Interaction):
            return
        if raw.type != _discord.InteractionType.component:
            return
        data = getattr(raw, 'data', {}) or {}
        cid = data.get('custom_id', '')
        if not cid.startswith('economy_buy_'):
            return

        item_id = cid.replace('economy_buy_', '')
        item = _find_item(item_id)
        uid = str(raw.user.id)
        uname = raw.user.display_name

        if not item:
            await raw.response.send_message(f"商品「{item_id}」不存在或已下架～", ephemeral=True)
            return

        bal = self.db.get_balance(uid)
        if bal < item["price"]:
            shortfall = item["price"] - bal
            await raw.response.send_message(
                f"{self.currency_emoji} 余额不足！\n**{item['name']}** 需要 {item['price']}{self.currency_name}，"
                f"你只有 {bal}{self.currency_name}，还差 {shortfall}{self.currency_name}～",
                ephemeral=True,
            )
            return

        new_bal = self.db.remove_coins(uid, item["price"], f"购买 {item['name']}")
        if new_bal is None:
            await raw.response.send_message("购买失败，请稍后再试。", ephemeral=True)
            return

        self.db.add_to_inventory(uid, item["id"])
        ab = item.get("affection_bonus", 0)
        if item["effect"] == "gift" and ab > 0:
            self._add_affection(uid, ab, f"赠送 {item['name']}")

        await raw.response.send_message(
            f"## ✅ 购买成功\n\n{uname} 购买了 **{item['emoji']} {item['name']}**\n"
            f"花费 {item['price']}{self.currency_name} → 余额 **{new_bal}**{self.currency_name}"
            + (f"\n💕 好感度 +{ab}" if ab > 0 else ""),
            ephemeral=True,
        )

    # ==================== /购买 ====================

    @filter.command("buy", alias={"购买"})
    async def cmd_buy(self, event: AstrMessageEvent, 商品名或编号: str = ""):
        """购买商店商品——输入编号或名称即可"""
        uid = event.get_sender_id()
        uname = event.get_sender_name() or "你"
        bot_name, _ = await self._get_persona(event)

        if not 商品名或编号.strip():
            if not await self._send_ephemeral(event, "想买什么？用 `/shop` 看看有什么好东西吧～"):
                yield event.plain_result("想买什么？用 `/shop` 看看有什么好东西吧～")
            return

        item = _find_item(商品名或编号)
        if not item:
            if not await self._send_ephemeral(event, f"没找到「{商品名或编号}」呢，用 `/shop` 看看有哪些商品吧～"):
                yield event.plain_result(f"没找到「{商品名或编号}」呢，用 `/shop` 看看有哪些商品吧～")
            return

        bal = self.db.get_balance(uid)
        if bal < item["price"]:
            shortfall = item["price"] - bal
            if not await self._send_ephemeral(event,
                f"{self.currency_emoji} 余额不足！\n\n"
                f"**{item['emoji']} {item['name']}** 需要 {item['price']}{self.currency_name}\n"
                f"你只有 **{bal}**{self.currency_name}，还差 **{shortfall}**{self.currency_name}\n\n"
                f"*去 `/work` 赚点钱吧～*"
            ):
                yield event.plain_result(
                    f"{self.currency_emoji} 余额不足！\n\n"
                    f"**{item['emoji']} {item['name']}** 需要 {item['price']}{self.currency_name}\n"
                    f"你只有 **{bal}**{self.currency_name}，还差 **{shortfall}**{self.currency_name}\n\n"
                    f"*去 `/work` 赚点钱吧～*"
                )
            return

        new_bal = self.db.remove_coins(uid, item["price"], f"购买 {item['name']}")
        if new_bal is None:
            if not await self._send_ephemeral(event, "购买失败，请稍后再试。"):
                yield event.plain_result("购买失败，请稍后再试。")
            return

        self.db.add_to_inventory(uid, item["id"])

        # ---- 好感度联动 ----
        affection_msg = ""
        ab = item.get("affection_bonus", 0)
        if item["effect"] == "gift" and ab > 0:
            affection_msg += self._add_affection(uid, ab, f"赠送 {item['name']}")

        # 效果描述
        effect_lines = []
        if item["effect"] == "gift":
            effect_lines.append(f"💕 {bot_name}收到了你的 **{item['emoji']} {item['name']}**！")
            if ab > 0:
                effect_lines.append(f"   好感度 **+{ab}** ✨")
        elif item["effect"] == "unlock_memory":
            effect_lines.append("💎 记忆水晶已激活！Bot会开始记住关于你的事。")
        elif item["effect"] == "clear_memory":
            effect_lines.append("🧪 遗忘药水已生效，Bot关于你的记忆被清除了。")
        elif item["effect"] == "random":
            r = random.randint(1, 100)
            if r >= 80:
                effect_lines.append(f"🍀 幸运符大吉！获得额外 {r}{self.currency_name} 的惊喜奖励！")
                self.db.add_coins(uid, r, "幸运符奖励")
            elif r <= 20:
                effect_lines.append(f"🍀 幸运符似乎不太灵验...（效果值: {r}）")
            else:
                effect_lines.append(f"🍀 幸运符闪烁了一下...（效果值: {r}）")

        lines = [
            f"## ✅ 购买成功",
            "",
            f"{uname} 购买了 **{item['emoji']} {item['name']}**",
            f"花费: {item['price']}{self.currency_name} → 余额: **{new_bal}**{self.currency_name}",
            "",
        ] + effect_lines

        if not await self._send_ephemeral(event, "\n".join(lines)):
            yield event.plain_result("\n".join(lines))

    # ==================== /交易记录 ====================

    @filter.command("transactions", alias={"交易记录"})
    async def cmd_transactions(self, event: AstrMessageEvent):
        """查看最近的交易记录"""
        uid = event.get_sender_id()
        uname = event.get_sender_name() or "你"
        txns = self.db.get_recent_transactions(uid, self.transaction_limit)

        if not txns:
            if not await self._send_ephemeral(event, f"📜 {uname} 还没有任何交易记录呢～"):
                yield event.plain_result(f"📜 {uname} 还没有任何交易记录呢～")
            return

        total_in = sum(t["amount"] for t in txns if t["amount"] > 0)
        total_out = sum(-t["amount"] for t in txns if t["amount"] < 0)

        lines = [
            f"# 📜 {uname} 的交易记录",
            f"*最近 {len(txns)} 笔 | 收入 {total_in} / 支出 {total_out}*",
            "",
        ]
        for t in txns:
            amt = t["amount"]
            sign = "+" if amt >= 0 else ""
            lines.append(f"{sign}{amt} {self.currency_emoji} — {t['reason']}")
            lines.append(f"  _{t['created_at']}_")

        if not await self._send_ephemeral(event, "\n".join(lines)):
            yield event.plain_result("\n".join(lines))

    # ==================== 好感度联动 ====================

    def _add_affection(self, user_id: str, points: int, reason: str) -> str:
        """跨插件写入好感度 DB"""
        if not os.path.exists(self._affection_db_path):
            return ""
        try:
            conn = sqlite3.connect(self._affection_db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT affection_points FROM affection WHERE user_id = ?", (user_id,)
                ).fetchone()
                cur = row["affection_points"] if row else 0
                new_pts = cur + points
                conn.execute(
                    "INSERT INTO affection (user_id, affection_points, last_interact) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET affection_points = ?",
                    (user_id, new_pts, datetime.now(BEIJING_TZ).isoformat(), new_pts),
                )
                conn.commit()
                return f"好感度 +{points}"
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[Economy] 好感度联动失败: {e}")
            return ""

    async def terminate(self):
        logger.info("[Economy] 插件已卸载")
