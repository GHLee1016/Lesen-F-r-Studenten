from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, Query
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Optional
import io, csv, json, time
from pathlib import Path


ART_DIR = Path(__file__).parent / "articles"

app = FastAPI()

# CORS (개발 기본값)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://localhost:8080",
        "https://127.0.0.1:8080",
        "http://localhost:8443",
        "http://127.0.0.1:8443",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 세션 메모리
SESSIONS: Dict[str, Dict] = {}
CLIENTS: Dict[str, WebSocket] = {}
EMA_ALPHA = 0.2

# 관리자(레벨/실시간)
ADMIN_TOKEN = "goethe-literacy-admin-token"
ADMIN_CLIENTS: List[WebSocket] = []
LEVEL_CFG = {"high": 70.0, "low": 40.0}

def require_admin(x_admin_token: str = Header(...)):
    if x_admin_token != ADMIN_TOKEN:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="invalid admin token")

def ensure_session(user_id: str):
    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"points": [], "scores": [], "ema": 50.0, "variant": "b1"}

def update_ema(user_id: str, score: float):
    prev = SESSIONS[user_id].get("ema", 50.0)
    ema = EMA_ALPHA * score + (1 - EMA_ALPHA) * prev
    SESSIONS[user_id]["ema"] = ema
    return ema

def groups_by_level(high=None, low=None):
    if high is None: high = LEVEL_CFG["high"]
    if low  is None: low  = LEVEL_CFG["low"]
    groups = {"high": [], "medium": [], "low": []}
    for uid, sess in SESSIONS.items():
        ema = sess.get("ema", 50.0)
        if ema >= high: lv = "high"
        elif ema < low: lv = "low"
        else: lv = "medium"
        groups[lv].append({"user_id": uid, "ema": round(ema, 1), "variant": sess.get("variant", "b1")})
    for k in groups:
        groups[k].sort(key=lambda x: x["ema"], reverse=True)
    return groups

async def broadcast_admin(payload: dict):
    dead = []
    for ws in ADMIN_CLIENTS:
        try: await ws.send_json(payload)
        except: dead.append(ws)
    for d in dead:
        try: ADMIN_CLIENTS.remove(d)
        except: pass

async def broadcast_users():
    payload = {"type": "users", "users": list(CLIENTS.keys())}
    dead = []
    for uid, ws in list(CLIENTS.items()):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(uid)
    # cleanup any dead sockets
    for uid in dead:
        try: CLIENTS.pop(uid, None)
        except: pass

# 기사 소스
ARTICLE_MAP = {
    "b1": """<h1>Achtung, schwarze Katze!?</h1>
<p>An Halloween sieht man sie überall: schwarze Katzen. Viele Menschen denken, dass sie Unglück bringen. Aber in manchen Ländern ist es genau anders herum. Wie kam das und was bedeutet das heute noch für diese Tiere?</p>
<p>Am 31. Oktober, an Halloween, sehen wir schwarze Katzen vor allem als Deko oder Kostüm. Ihr dunkles Fell und die geheimnisvollen Augen wirken auf viele Menschen etwas gruselig. Wenn eine schwarze Katze vor einem über die Straße läuft, denken viele, dass etwas Schlechtes passiert. Aber schon lange vor Halloween haben Menschen schwarze Katzen mit Angst und dem Bösen verbunden. Halloween wurde erst im 19. Jahrhundert in den USA bekannt. Im Mittelalter dachten die Menschen, schwarze Katzen gehören zum Teufel. Sie brachten sie mit Hexen zusammen. Viele schwarze Katzen wurden damals verfolgt und verbrannt. Das passierte an manchen Orten sogar bis ins 18. Jahrhundert.</p>
<p>Aber nicht überall bringen schwarze Katzen Unglück. Im alten Ägypten sahen die Menschen sie als heilig an. Sie verehrten die Göttin Bastet, die schwangere Frauen, Kinder und Mütter beschützt. In Großbritannien und Irland soll es Glück bringen, wenn man einer schwarzen Katze begegnet. In Japan sollen sie vor Krankheiten schützen. Außerdem sollen sie Frauen bei der Liebe helfen. Auch in Filmen und Serien gibt es sie als Schutz-Symbol. Ein Beispiel ist "Luna" aus der japanischen Serie "Sailor Moon". Das ist eine sprechende schwarze Katze, die die Heldinnen beschützt.</p>
<p>Für die schwarze Farbe des Fells ist ein bestimmtes Gen verantwortlich. Es heißt B-Gen. Dieses Gen macht ein dunkles Farbmittel. Dadurch wird das Fell, oft auch Nase und Pfoten, schwarz. Schwarze Katzen sind auch öfter männlich. Das liegt daran, dass das Gen auf einem bestimmten Chromosom liegt, dem X-Chromosom. Wissenschaftler haben auch herausgefunden, dass dieses Gen die Tiere besser vor Krankheiten schützt. Außerdem ist das dunkle Fell nützlich, wenn die Katzen nachts Mäuse jagen.</p>
<p>Die dunkle Farbe kann aber auch ein Problem sein. Das passiert besonders dann, wenn diese Katzen ein neues Zuhause suchen. Der Deutsche Tierschutzbund hat 2020 eine Umfrage gemacht. 48 Prozent der Tierheime sagten, dass schwarze Katzen schwerer ein neues Zuhause finden. Viele Menschen finden sie nicht so schön. Außerdem kann man sie nicht so gut für Fotos in sozialen Netzwerken fotografieren. Nur an Halloween interessieren sich mehr Menschen für sie. Aber gerade dann wollen viele Tierheime keine schwarzen Katzen vermitteln. Sie wollen die Tiere schützen. Sie sollten nicht als Deko benutzt werden oder sogar für seltsame Rituale missbraucht werden.</p>
"""
}

@app.get("/article")
def get_article(v: Optional[str] = Query("b1")):
    html = ARTICLE_MAP.get(v or "b1", ARTICLE_MAP["b1"])
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    user_id = ws.query_params.get("user_id")
    variant = ws.query_params.get("variant", "b1")
    await ws.accept()
    if not user_id:
        await ws.close(code=4400)
        return
    CLIENTS[user_id] = ws
    ensure_session(user_id)
    SESSIONS[user_id]["variant"] = variant
    try:
        # notify all clients and admins about user list/group changes
        await broadcast_users()
        await broadcast_admin({"type": "groups", "data": groups_by_level()})
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            typ = data.get("type")
            if typ == "gaze":
                t = int(data["t"]); x = float(data["x"]); y = float(data["y"])
                SESSIONS[user_id]["points"].append((t, x, y))
            elif typ == "score":
                t = int(data["t"]); s = float(data["score"])
                SESSIONS[user_id]["scores"].append((t, s))
                ema = update_ema(user_id, s)
                await broadcast_admin({"type": "score_update", "user_id": user_id, "ema": round(ema,1)})
    except WebSocketDisconnect:
        pass
    finally:
        CLIENTS.pop(user_id, None)
        # notify on disconnect as well
        await broadcast_users()
        await broadcast_admin({"type": "groups", "data": groups_by_level()})

@app.websocket("/admin/ws")
async def admin_ws(ws: WebSocket):
    await ws.accept()
    ADMIN_CLIENTS.append(ws)
    await ws.send_json({"type": "groups", "data": groups_by_level()})
    try:
        while True:
            await ws.receive_text()  # keepalive
    except WebSocketDisconnect:
        pass
    finally:
        try: ADMIN_CLIENTS.remove(ws)
        except: pass

@app.get("/users")
def get_users():
    return {"users": list(CLIENTS.keys())}

@app.get("/export/{user_id}.csv")
def export_csv(user_id: str):
    sess = SESSIONS.get(user_id)
    if not sess:
        return JSONResponse({"error":"no data"}, status_code=404)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["t_ms","x_px","y_px"])
    for t,x,y in sess["points"]:
        w.writerow([t,x,y])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{user_id}.csv"'}
    )

@app.get("/export/{user_id}.json")
def export_json(user_id: str):
    sess = SESSIONS.get(user_id)
    if not sess:
        return JSONResponse({"error":"no data"}, status_code=404)
    payload = {
        "user_id": user_id,
        "variant": sess.get("variant","b1"),
        "ema": sess.get("ema", 50.0),
        "points": [{"t":t,"x":x,"y":y} for t,x,y in sess["points"]],
        "scores": [{"t":t,"score":s} for t,s in sess.get("scores", [])],
        "exported_at": int(time.time()*1000),
    }
    data = json.dumps(payload, ensure_ascii=False)
    return StreamingResponse(iter([data]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{user_id}.json"'}
    )

@app.get("/admin/levels")
def admin_levels(_: None = Depends(require_admin)):
    return groups_by_level()

@app.get("/admin/levels/export.csv")
def admin_levels_csv(_: None = Depends(require_admin)):
    groups = groups_by_level()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["level","user_id","ema","variant"])
    for lv, items in groups.items():
        for it in items:
            w.writerow([lv, it["user_id"], it["ema"], it["variant"]])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="levels.csv"'}
    )

@app.get("/admin/levels/export.json")
def admin_levels_json(_: None = Depends(require_admin)):
    js = json.dumps(groups_by_level(), ensure_ascii=False)
    return StreamingResponse(iter([js]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="levels.json"'}
    )

@app.post("/admin/levels/config")
def admin_levels_cfg(cfg: dict, _: None = Depends(require_admin)):
    hi = float(cfg.get("high", LEVEL_CFG["high"]))
    lo = float(cfg.get("low", LEVEL_CFG["low"]))
    if not (0 <= lo < hi <= 100):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="invalid thresholds")
    LEVEL_CFG["high"], LEVEL_CFG["low"] = hi, lo
    import anyio
    anyio.from_thread.run(broadcast_admin, {"type":"groups","data": groups_by_level(hi, lo)})
    return {"ok": True, "config": LEVEL_CFG}

# ===== Additions: Calibration, Heatmap export with presets, AOI stats =====
from typing import Tuple
from math import sqrt
try:
    from PIL import Image, ImageDraw
except Exception as e:
    Image = None
    ImageDraw = None

# In-memory calibration and AOI configs
CALIB: Dict[str, Dict] = {}      # {user_id: {"model":"affine","coef":{"ax":[...],"ay":[...]}, "rmse":float, "n":int}}
AOI_CFG: Dict[str, Dict] = {}    # {user_id: {"aois":[{"id":str,"name":str,"poly":[[x,y],...] }]}}

def apply_affine(x: float, y: float, coef: Dict[str, List[float]]) -> Tuple[float,float]:
    ax = coef["ax"]; ay = coef["ay"]
    x2 = ax[0]*x + ax[1]*y + ax[2]
    y2 = ay[0]*x + ay[1]*y + ay[2]
    return x2, y2

def fit_affine(src_xy: List[Tuple[float,float]], dst_xy: List[Tuple[float,float]]):
    # Solve least squares for 2x3 affine: [x y 1] * [[a00 a01],[a10 a11],[a20 a21]] -> [x', y']
    import numpy as np
    A = []
    bx = []
    by = []
    for (x,y),(u,v) in zip(src_xy, dst_xy):
        A.append([x,y,1.0])
        bx.append(u); by.append(v)
    A = np.array(A, dtype=float)
    bx = np.array(bx, dtype=float); by = np.array(by, dtype=float)
    ax, *_ = np.linalg.lstsq(A, bx, rcond=None)
    ay, *_ = np.linalg.lstsq(A, by, rcond=None)
    return ax.tolist(), ay.tolist()

def rmse(a: List[Tuple[float,float]], b: List[Tuple[float,float]]):
    s = 0.0
    for (x1,y1),(x2,y2) in zip(a,b):
        dx = x1-x2; dy = y1-y2
        s += dx*dx + dy*dy
    return (s/ max(1,len(a))) ** 0.5

# Calibration endpoints
@app.post("/calibration/{user_id}/start")
def calib_start(user_id: str, cfg: dict):
    # cfg: {"n_points":5..9, "model":"affine"}
    n = int(cfg.get("n_points", 5))
    n = max(5, min(9, n))
    model = (cfg.get("model") or "affine").lower()
    CALIB[user_id] = {"samples": [], "n_points": n, "model": model}
    return {"ok": True, "n_points": n, "model": model}

@app.post("/calibration/{user_id}/sample")
def calib_sample(user_id: str, payload: dict):
    # payload: {"target":[x,y], "obs":[x,y], "t":ms}
    c = CALIB.setdefault(user_id, {"samples": []})
    c["samples"].append({"target": payload["target"], "obs": payload["obs"], "t": int(payload.get("t", time.time()*1000))})
    return {"ok": True, "count": len(c["samples"])}

@app.post("/calibration/{user_id}/finish")
def calib_finish(user_id: str):
    c = CALIB.get(user_id)
    if not c or not c.get("samples"):
        return JSONResponse({"error":"no samples"}, status_code=400)
    src = [(s["obs"][0], s["obs"][1]) for s in c["samples"]]
    dst = [(s["target"][0], s["target"][1]) for s in c["samples"]]
    ax, ay = fit_affine(src, dst)
    pred = [apply_affine(x,y, {"ax":ax,"ay":ay}) for x,y in src]
    e = rmse(pred, dst)
    CALIB[user_id].update({"model":"affine", "coef":{"ax":ax,"ay":ay}, "rmse": e, "n": len(src)})
    return {"ok": True, "model":"affine", "coef":{"ax":ax,"ay":ay}, "rmse": round(e,2), "n": len(src)}

@app.get("/calibration/{user_id}")
def calib_status(user_id: str):
    return CALIB.get(user_id, {"status":"none"})

# AOI handling
def point_in_poly(x: float, y: float, poly: List[List[float]]) -> bool:
    # ray casting
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i+1)%n]
        if ((y1>y) != (y2>y)):
            xints = (x2-x1)*(y - y1) / (y2 - y1 + 1e-12) + x1
            if x < xints: inside = not inside
    return inside

@app.post("/aoi/{user_id}/set")
def aoi_set(user_id: str, cfg: dict):
    # cfg: {"aois":[{"id": "A1", "name":"title", "poly":[[x,y], ...]}, ...]}
    AOI_CFG[user_id] = {"aois": cfg.get("aois", [])}
    return {"ok": True, "count": len(AOI_CFG[user_id]["aois"])}

@app.get("/aoi/{user_id}/stats")
def aoi_stats(user_id: str):
    sess = SESSIONS.get(user_id)
    if not sess: return {"aois": [], "total_points": 0}
    aois = AOI_CFG.get(user_id, {}).get("aois", [])
    pts = sess.get("points", [])
    if not aois or not pts: return {"aois": [{"id":a["id"],"name":a.get("name",""),"entries":0,"dwell_ms":0} for a in aois], "total_points": len(pts)}
    # compute entries and dwell using inter-sample dt
    # assume points are ordered by time
    res = {a["id"]: {"id":a["id"], "name": a.get("name",""), "entries":0, "dwell_ms":0} for a in aois}
    prev_hit = None
    for i,(t,x,y) in enumerate(pts):
        t2 = pts[i+1][0] if i+1 < len(pts) else t
        dt = max(0, t2 - t)
        hit_any = None
        for a in aois:
            if point_in_poly(x,y, a["poly"]):
                res[a["id"]]["dwell_ms"] += dt
                hit_any = a["id"]
        if hit_any != prev_hit and hit_any is not None:
            res[hit_any]["entries"] += 1
        prev_hit = hit_any
    return {"aois": list(res.values()), "total_points": len(pts)}

# Path SVG export
@app.get("/export/{user_id}.path.svg")
def export_path_svg(user_id: str, stroke: float = 2.0, color: str = "#2b8cff", w: Optional[int]=None, h: Optional[int]=None, margin: int=16, bg: Optional[str]=None):
    sess = SESSIONS.get(user_id)
    if not sess: return JSONResponse({"error":"no data"}, status_code=404)
    pts = sess.get("points", [])
    if not pts: return JSONResponse({"error":"empty"}, status_code=400)
    # apply calibration if exists
    c = CALIB.get(user_id,{}) ; coef = c.get("coef")
    xy = []
    xs=[]; ys=[]
    for t,x,y in pts:
        if coef: x,y = apply_affine(x,y, coef)
        xs.append(x); ys.append(y); xy.append((x,y))
    if w is None: w = int((max(xs)-min(xs))+margin*2) or 100
    if h is None: h = int((max(ys)-min(ys))+margin*2) or 100
    minx, miny = min(xs), min(ys)
    # build SVG polyline
    def esc(s): return s.replace('"','&quot;') if isinstance(s,str) else s
    path_d = " ".join(f"L {x-minx+margin:.1f} {y-miny+margin:.1f}" for x,y in xy)
    if path_d.startswith("L"): path_d = "M" + path_d[1:]
    bg_rect = f'<rect x="0" y="0" width="{w}" height="{h}" fill="{esc(bg)}"/>' if bg else ""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
{bg_rect}<path d="{path_d}" fill="none" stroke="{esc(color)}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round"/></svg>'''
    return StreamingResponse(iter([svg]), media_type="image/svg+xml", headers={"Content-Disposition": f'attachment; filename="{user_id}.path.svg"'} )

# Heatmap export with presets
def colormap_turbo(v: float):
    # v in [0,1] → RGB tuple; simple piecewise approx
    t = max(0.0, min(1.0, v))
    # coefficients from turbo colormap approximation
    r = 34.61 + t*(1172.33 + t*(-10733.56 + t*(33300.12 + t*(-38394.49 + t*15064.50))))
    g = 23.31 + t*(557.33 + t*(1225.33 + t*(-3574.35 + t*(2630.13 + t*(-492.11)))))
    b = 27.20 + t*(321.09 + t*( -1525.90 + t*(4490.55 + t*(-4267.43 + t*1256.30))))
    return (int(max(0,min(255,r))), int(max(0,min(255,g))), int(max(0,min(255,b))))

def colormap_gray(v: float):
    g = int(max(0,min(255, round(v*255)))); return (g,g,g)

def normalize(values, mode="minmax"):
    mv = [v for v in values if v is not None]
    if not mv: return values
    if mode=="zscore":
        import statistics as st
        mu = st.mean(mv); sd = max(1e-6, st.pstdev(mv))
        return [(v-mu)/sd*0.2 + 0.5 for v in values]  # squeeze to ~[0,1]
    elif mode=="maxabs":
        m = max(abs(v) for v in mv) or 1.0
        return [v/m for v in values]
    else:
        lo = min(mv); hi = max(mv); span = hi-lo or 1.0
        return [(v-lo)/span for v in values]

@app.get("/export/{user_id}.heatmap.png")
def export_heatmap(user_id: str, w: Optional[int]=None, h: Optional[int]=None, radius: int=24, point_intensity: float=1.0, margin: int=16, colormap: str="turbo", gamma: float=1.0, norm: str="minmax", bg: Optional[str]=None):
    if Image is None:
        return JSONResponse({"error":"Pillow not installed"}, status_code=500)
    sess = SESSIONS.get(user_id)
    if not sess: return JSONResponse({"error":"no data"}, status_code=404)
    pts = sess.get("points", [])
    if not pts: return JSONResponse({"error":"empty"}, status_code=400)
    c = CALIB.get(user_id,{}) ; coef = c.get("coef")
    xs=[]; ys=[]; xy=[]
    for t,x,y in pts:
        if coef: x,y = apply_affine(x,y, coef)
        xs.append(x); ys.append(y); xy.append((x,y))
    if w is None: w = int((max(xs)-min(xs))+margin*2) or 100
    if h is None: h = int((max(ys)-min(ys))+margin*2) or 100
    minx, miny = min(xs), min(ys)

    # accumulate grayscale heat
    import math
    W,H = w,h
    acc = [[0.0]*W for _ in range(H)]
    rad = max(3, int(radius))
    for (x,y) in xy:
        cx = int(round(x - minx + margin)); cy = int(round(y - miny + margin))
        if cx<0 or cy<0 or cx>=W or cy>=H: continue
        for yy in range(max(0, cy-rad), min(H, cy+rad+1)):
            dy = yy-cy
            for xx in range(max(0, cx-rad), min(W, cx+rad+1)):
                dx = xx-cx
                d2 = dx*dx + dy*dy
                if d2 <= rad*rad:
                    wgt = math.exp(-d2/(2*(rad*0.6)**2))  # gaussian-ish
                    acc[yy][xx] += wgt * point_intensity

    # flatten and normalize
    flat = [v for row in acc for v in row]
    flat = normalize(flat, mode=norm)
    # gamma correction
    if abs(gamma-1.0) > 1e-3:
        flat = [max(0.0, min(1.0, v))**gamma for v in flat]

    # colorize
    cm = colormap.lower()
    def mapcolor(v):
        if cm=="gray": return colormap_gray(v)
        else: return colormap_turbo(v)
    img = Image.new("RGBA", (W,H), (0,0,0,0))
    px = img.load()
    k=0
    for yy in range(H):
        for xx in range(W):
            r,g,b = mapcolor(flat[k]); k+=1
            px[xx,yy] = (r,g,b, 220)
    if bg:
        bgimg = Image.new("RGB",(W,H), bg)
        bgimg.paste(img, (0,0), img)
        out = bgimg
    else:
        out = img
    bio = io.BytesIO(); out.save(bio, format="PNG")
    bio.seek(0)
    return StreamingResponse(bio, media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{user_id}.heatmap.png"'})