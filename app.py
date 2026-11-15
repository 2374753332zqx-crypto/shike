"""
食刻 · 后端正转（参赛版）
--------------------------------------------------
职责：接收前端 base64 图片 → 调用华为云 MaaS 多模态模型 → 用本地营养库校准
     → 返回结构化 JSON 给前端。

密钥只存在服务端环境变量，前端永远拿不到。

运行：
    export MAAS_BASE_URL="https://api.modelarts-maas.com/v2"
    export MAAS_API_KEY="<你的API Key>"
    export MAAS_MODEL="deepseek-v4.1-flash-1017"
    pip install -r requirements.txt
    python app.py

注意：MAAS_BASE_URL 结尾必须是 /v2（V1 接口不支持 DeepSeek 系列，会返回 404
     ModelArts.81016）。脚本会自动在后面拼 /chat/completions，所以不要写完整路径。
"""

import base64
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR.parent                 # 演示页所在目录

MAAS_BASE_URL = os.environ.get("MAAS_BASE_URL", "").rstrip("/")
MAAS_API_KEY = os.environ.get("MAAS_API_KEY", "")
MAAS_MODEL = os.environ.get("MAAS_MODEL", "")
TIMEOUT = 45

logging.basicConfig(
    filename=str(BASE_DIR / "calls.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
app = Flask(__name__, static_folder=None)

# ---------- 营养库 ----------
def load_nutrition():
    """读取 nutrition.csv：名称,别名,千卡,碳水,蛋白,脂肪（均按 100g）"""
    table = {}
    path = BASE_DIR / "nutrition.csv"
    if not path.exists():
        return table
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        name, alias, kcal, carb, protein, fat = parts[:6]
        item = {
            "name": name,
            "per100": {"kcal": float(kcal), "carb": float(carb),
                       "protein": float(protein), "fat": float(fat)},
        }
        table[name] = item
        for a in alias.split("|"):
            if a:
                table[a] = item
    return table


NUTRITION = load_nutrition()

MODE_LABEL = {"balance": "均衡", "lowcarb": "低碳水", "highpro": "高蛋白"}


def pick_canonical(name: str):
    """把模型给的菜名映射到营养库条目（含同义词、模糊包含）"""
    if not name:
        return None, None
    key = re.sub(r"[\s（）()]+", "", name)
    if key in NUTRITION:
        return NUTRITION[key], key
    for alias, item in NUTRITION.items():
        if alias and (alias in key or key in alias):
            return item, item["name"]
    return None, None


def clamp_grams(g):
    try:
        g = float(g)
    except (TypeError, ValueError):
        return 150.0
    return max(50.0, min(300.0, g))


# ---------- 调模型 ----------
SYSTEM_PROMPT = """你是食堂营养分析助手。看图片识别餐盘里的食物，只输出 JSON，不要任何解释。
要求：
1. 只识别能看清的食物，最多 5 项；
2. grams 用食堂常见份量估算（一拳米饭约 150g，一份炒菜约 120g，一个鸡腿约 100g）；
3. confidence 取 0~1；
4. 输出格式：
{"dishes":[{"name":"白米饭","grams":150,"confidence":0.93}]}
如果图里没有食物，输出 {"dishes":[]}。"""


def call_maas(image_b64: str):
    if not (MAAS_BASE_URL and MAAS_API_KEY and MAAS_MODEL):
        raise RuntimeError("未配置 MAAS_BASE_URL / MAAS_API_KEY / MAAS_MODEL")

    payload = {
        "model": MAAS_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "识别这份餐盘，按约定输出 JSON。"},
                {"type": "image_url", "image_url": {"url": image_b64}},
            ]},
        ],
        "temperature": 0.2,
    }
    started = time.time()
    resp = requests.post(
        f"{MAAS_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {MAAS_API_KEY}",
                 "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    logging.info("MaaS ok %.1fs usage=%s", time.time() - started,
                 data.get("usage", {}))
    return data["choices"][0]["message"]["content"]


def parse_dishes(text: str):
    """模型输出不稳定，三层兜底：直接解析 → 截取花括号 → 正则捞菜名"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            names = re.findall(r"“?([\u4e00-\u9fa5]{2,6}菜|[\u4e00-\u9fa5]{2,4})”?", text)
            return [{"name": n, "grams": 150, "confidence": 0.5} for n in names[:5]]
    return obj.get("dishes", [])[:5]


# ---------- 核算与建议 ----------
def analyze(dishes, goal):
    out = []
    total = {"kcal": 0.0, "carb": 0.0, "protein": 0.0, "fat": 0.0}
    for d in dishes:
        name = d.get("name", "")
        item, canonical = pick_canonical(name)
        grams = clamp_grams(d.get("grams"))
        if item:
            per100 = item["per100"]
            kcal = per100["kcal"] * grams / 100
            carb = per100["carb"] * grams / 100
            protein = per100["protein"] * grams / 100
            fat = per100["fat"] * grams / 100
        else:
            kcal, carb, protein, fat = 150 * grams / 100, 15 * grams / 100, 6 * grams / 100, 6 * grams / 100
        for k, v in (("kcal", kcal), ("carb", carb), ("protein", protein), ("fat", fat)):
            total[k] += v
        out.append({
            "name": canonical or name,
            "grams": round(grams),
            "kcal": round(kcal),
            "carb": round(carb, 1),
            "protein": round(protein, 1),
            "fat": round(fat, 1),
            "confidence": round(float(d.get("confidence", 0.6)), 2),
            "matched": bool(item),
        })
    return out, {k: round(v, 1) for k, v in total.items()}


def build_advice(total, dishes, goal, mode):
    carb_t, pro_t, fat_t = goal * 0.125, goal * 0.0375, goal * 0.0335
    rice_carb = sum(d["carb"] for d in dishes if "米饭" in d["name"])
    rice_share = (rice_carb / total["carb"] * 100) if total["carb"] else 0
    pro_gap = max(0, pro_t - total["protein"])

    if mode == "lowcarb":
        lead = f"碳水 {total['carb']:.0f} g，其中 {rice_share:.0f}% 来自米饭。"
        items = [
            {"t": "米饭减半", "d": "热量约 -87 kcal，碳水 -19 g，体感几乎无差", "v": "-87 kcal"},
            {"t": "蛋白别再靠主食", "d": "鸡腿去皮 + 一份豆腐，把饱腹感换到蛋白质", "v": f"+{min(9, pro_gap):.0f} g 蛋白"},
            {"t": "目标", "d": "一餐碳水压到 35 g 上下，血糖曲线更平", "v": "目标 35 g"},
        ]
    elif mode == "highpro":
        lead = f"蛋白质 {total['protein']:.1f} g，距今日目标还差 {pro_gap:.0f} g。"
        items = [
            {"t": "先加一个水煮蛋", "d": "+7 g 蛋白、+78 kcal，窗口基本都有", "v": "+7 g"},
            {"t": "再来一杯无糖酸奶", "d": "训练日可一次补齐缺口", "v": "+6 g"},
            {"t": "换主菜", "d": "鸡腿换鸡胸，或加一份豆制品", "v": "-5 g 脂肪"},
        ]
    else:
        lead = (f"本餐 {total['kcal']:.0f} kcal，蛋白质达今日目标 "
                f"{total['protein'] / pro_t * 100:.0f}%，短板在蔬菜。")
        items = [
            {"t": "蔬菜偏少", "d": "建议一餐 200 g，深色蔬菜优先", "v": "+28 kcal"},
            {"t": "钠偏高", "d": "食堂菜偏咸，餐后多喝水，别再喝汤底", "v": "—"},
            {"t": "想控体重", "d": "切到低碳水模式，看第 2 条建议", "v": "—"},
        ]
    return {
        "lead": lead,
        "items": items,
        "targets": {"carb": round(carb_t), "protein": round(pro_t), "fat": round(fat_t)},
        "share": round(total["kcal"] / goal * 100, 1) if goal else 0,
    }


# ---------- 路由 ----------
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return resp


@app.route("/api/analyze", methods=["POST", "OPTIONS"])
def api_analyze():
    if request.method == "OPTIONS":
        return ("", 204)

    body = request.get_json(silent=True) or {}
    image = body.get("image", "")
    goal = int(body.get("goal", 2000))
    mode = body.get("mode", "balance")

    if not image.startswith("data:image/"):
        return jsonify({"error": "image 必须是 data:image/* 的 base64"}), 400
    if len(image) > 8 * 1024 * 1024:
        return jsonify({"error": "图片过大，请先压缩到长边 1024"}), 413

    try:
        content = call_maas(image)
        dishes = parse_dishes(content)
    except Exception as exc:                      # 模型/网络失败 → 明确回落
        logging.warning("MaaS failed: %s", exc)
        return jsonify({"error": "识别服务暂不可用", "fallback": True,
                        "detail": str(exc)[:120]}), 502

    if not dishes:
        return jsonify({"error": "没能识别出食物", "fallback": True}), 422

    detail, total = analyze(dishes, goal)
    advice = build_advice(total, detail, goal, mode)
    return jsonify({"dishes": detail, "total": total, "advice": advice,
                    "mode": mode, "modeLabel": MODE_LABEL.get(mode, "均衡"),
                    "fallback": False})


@app.route("/api/health")
def health():
    ready = bool(MAAS_BASE_URL and MAAS_API_KEY and MAAS_MODEL)
    return jsonify({"ready": ready, "nutrition": len(NUTRITION),
                    "model": MAAS_MODEL or None})


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "食堂营养分析工作台.html")


if __name__ == "__main__":
    print("营养库条目:", len(NUTRITION))
    app.run(host="0.0.0.0", port=8000, debug=False)
#（注：内容由AI生成）
