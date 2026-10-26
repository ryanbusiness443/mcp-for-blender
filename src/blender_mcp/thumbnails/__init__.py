"""YouTube thumbnails rendered in Blender, in the style top creators use.

`thumb3d.py` is a self-contained Blender script: a few huge words with a flat bright face, a solid 3D
block behind, thick black outline and hard drop shadow; one subject that pops (a transparent cut-out
photo and/or photoreal 3D props from Poly Haven) with a white stroke and a coloured halo; the
backdrop graded and darkened behind the text. It renders with Cycles, headless
(`blender -b --python thumb3d.py -- spec.json`) or in the user's live Blender through execute_code,
where it builds in a new scene and leaves the user's scenes alone.
"""
import datetime
import json
import os
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("YT_THUMB_CACHE", Path.home() / ".cache" / "mcp-for-blender" / "thumbnails"))
UA = {"User-Agent": "mcp-for-blender-thumbnails/1.0"}

FONTS = {
    "lilita": "LilitaOne-Regular.ttf",      # rounded heavy, the classic gaming-thumbnail look (default)
    "anton": "Anton-Regular.ttf",           # tall condensed, serious / news
    "bebas": "BebasNeue-Regular.ttf",       # clean condensed caps
    "titan": "TitanOne-Regular.ttf",        # chunky, playful
    "archivo": "ArchivoBlack-Regular.ttf",  # wide heavy sans
    "luckiest": "LuckiestGuy-Regular.ttf",  # cartoon / horror
}
FACES = ("flat", "gold", "chrome", "fire", "ice", "toxic")
# Poly Haven models (CC0) that frame well as a single hero prop.
PROPS = ("treasure_chest", "ammo_box", "wooden_crate_01", "stick_grenade")
PRESETS = {
    # white text, one word in yellow, black block / outline / shadow: what most top creators use
    "creator": {"face": "flat", "text_color": "#ffffff", "highlight_color": "#ffe600", "glow": "#ffb800", "font": "lilita"},
    "red": {"face": "flat", "text_color": "#ffffff", "highlight_color": "#ff2e2e", "glow": "#ff2e2e", "font": "lilita"},
    "horror": {"face": "flat", "text_color": "#ffffff", "highlight_color": "#ff2e2e", "glow": "#8b0000", "font": "luckiest", "depth_color": "#2a0000"},
    "fire": {"face": "fire", "highlight_color": "#ffffff", "glow": "#ff6a00", "font": "titan", "depth_color": "#3a0a00"},
    "ice": {"face": "ice", "highlight_color": "#ffffff", "glow": "#4dd2ff", "font": "titan", "depth_color": "#06203f"},
    "money": {"face": "flat", "text_color": "#ffffff", "highlight_color": "#3cff5a", "glow": "#3cff5a", "font": "archivo"},
    "clean": {"face": "flat", "text_color": "#ffffff", "highlight_color": "#ffe600", "glow": False, "font": "bebas", "shadow": 0.5},
}


def script_source() -> str:
    return (HERE / "thumb3d.py").read_text(encoding="utf-8")


def font_path(name):
    """A bundled font by short name, or a path to any .ttf/.otf."""
    if not name:
        name = "lilita"
    if os.path.exists(name):
        return name
    return str(HERE / "fonts" / FONTS.get(name.lower(), FONTS["lilita"]))


def _get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60)


def polyhaven_model(asset_id: str, res: str = "1k") -> str:
    """Download (once) a Poly Haven model as glTF and return the .gltf path."""
    folder = CACHE / "models" / asset_id
    existing = list(folder.glob("*.gltf"))
    if existing:
        return str(existing[0])
    files = json.load(_get(f"https://api.polyhaven.com/files/{asset_id}"))
    g = files["gltf"][res]["gltf"]
    folder.mkdir(parents=True, exist_ok=True)
    for rel, inc in g.get("include", {}).items():
        dst = folder / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(_get(inc["url"]).read())
    main = folder / os.path.basename(g["url"])
    main.write_bytes(_get(g["url"]).read())
    return str(main)


def studio_hdri() -> str | None:
    """A soft studio HDRI for realistic reflections (Poly Haven, CC0), downloaded once."""
    dst = CACHE / "hdri" / "studio_small_09_1k.hdr"
    if not dst.exists():
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(_get("https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/studio_small_09_1k.hdr").read())
        except Exception:
            return None  # the script falls back to a procedural studio
    return str(dst)


def default_output() -> str:
    folder = Path.home() / "Pictures" / "YouTube Thumbnails"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / f"thumbnail_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg")


def make_spec(title, highlight=None, preset="creator", face=None, text_color=None, highlight_color=None,
              font=None, badge=None, background_path=None, subject_path=None, props=None, side="right",
              glow=None, arrow_to=None, circle_at=None, circle_radius=0.1, output_path=None, samples=48,
              live=True):
    """Turn tool arguments into the spec thumb3d.py reads. Raises ValueError on bad input."""
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {sorted(PRESETS)}")
    p = PRESETS[preset]
    face = face or p.get("face", "flat")
    if face not in FACES:
        raise ValueError(f"face must be one of {list(FACES)}")
    if side not in ("left", "right"):
        raise ValueError("side must be left or right")
    for label, path in (("background_path", background_path), ("subject_path", subject_path)):
        if path and not os.path.exists(path):
            raise ValueError(f"{label} not found: {path}")
    words = title.replace("\\n", " ").split()
    if not highlight and len(words) >= 2:
        # the longest word usually carries the hook; on a tie, the later one (hooks come last)
        highlight = [max(reversed(words), key=lambda w: len(w.strip("!?.,'"))).strip("!?.,")]
    spec = {
        "out": output_path or default_output(),
        "title": title.replace("\\n", "\n"),
        "highlight": [h.upper() for h in (highlight or [])],
        "face": face,
        "text_color": text_color or p.get("text_color", "#ffffff"),
        "highlight_color": highlight_color or p.get("highlight_color", "#ffe600"),
        "depth_color": p.get("depth_color", "#101010"),
        "font": font_path(font or p.get("font")),
        "badge": badge,
        "side": side,
        "glow": glow if glow is not None else p.get("glow", "#ffb800"),
        "shadow": p.get("shadow", 0.8),
        "samples": samples,
        "fresh": not live,  # live Blender: build in a new scene, never reset the user's file
        "scene_name": "YouTube Thumbnail",
    }
    if background_path:
        spec["background"] = background_path
    if subject_path:
        spec["subject"] = subject_path
    if props:
        spec["props"] = [{"path": x if os.path.exists(x) else polyhaven_model(x)} for x in props]
    if props or subject_path:
        hdri = studio_hdri()
        if hdri:
            spec["hdri"] = hdri
    if arrow_to:
        spec["arrow"] = {"to": [float(arrow_to[0]), float(arrow_to[1])]}
    if circle_at:
        spec["circle"] = {"at": [float(circle_at[0]), float(circle_at[1])], "radius": circle_radius}
    return spec


def build_code(spec: dict) -> str:
    """Python to send through execute_code: the spec, then the script (which runs main(SPEC))."""
    return f"SPEC = {spec!r}\n" + script_source()
