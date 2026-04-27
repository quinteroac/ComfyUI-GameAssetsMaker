import json
import math
import os
import re
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

import cv2
import folder_paths
import numpy as np
import torch

try:
    from aiohttp import web
    from server import PromptServer
except Exception:
    web = None
    PromptServer = None


class SVGData:
    def __init__(self, data):
        self.data = data


def _validated_export_dir(export_dir):
    output_root = os.path.abspath(folder_paths.get_output_directory())
    requested = os.path.abspath(os.path.expanduser(str(export_dir or "")))
    if not requested or not os.path.isdir(requested):
        raise ValueError("Spine export directory does not exist")
    if os.path.commonpath([output_root, requested]) != output_root:
        raise ValueError("Spine export directory must be inside the ComfyUI output directory")
    return requested


async def _download_spine_export(request):
    try:
        export_dir = _validated_export_dir(request.query.get("dir", ""))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

    asset_name = os.path.basename(export_dir.rstrip(os.sep)) or "spine_export"
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for root, _dirs, files in os.walk(export_dir):
            for filename in files:
                file_path = os.path.join(root, filename)
                archive_name = os.path.relpath(file_path, export_dir)
                zip_file.write(file_path, archive_name)

    return web.Response(
        body=archive.getvalue(),
        content_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{asset_name}.zip"'},
    )


def _register_game_assets_routes():
    if PromptServer is None or web is None:
        return
    if getattr(PromptServer.instance, "_game_assets_maker_routes_registered", False):
        return
    PromptServer.instance.routes.get("/game_assets_maker/download_spine")(_download_spine_export)
    PromptServer.instance._game_assets_maker_routes_registered = True


_register_game_assets_routes()


SVG_DEPTH_ORDERS = ["back_to_front", "front_to_back", "input_order"]
SVG_CONTOUR_MODES = ["external", "hierarchy"]
SPINE_EXPORT_VERSION = "spine-export-v4-minimal-control-bones"
SPINE_ANIMATION_PRESETS = ["all", "full_idle", "idle_breath", "blink", "hair_sway", "face_alive", "no_animations"]
SPINE_CONTROL_BONE_ROLES = {"clothing", "torso", "head", "hair", "iris", "eyelash"}
SPINE_POINT_CONTROL_ROLES = {"hair", "iris", "eyelash"}
ASEPRITE_SPRITESHEET_TYPES = ["Half Body", "Full Body", "Face Expressions"]
ASEPRITE_LAYOUT_DIRECTIONS = ["Horizontal", "Vertical", "Grid"]
ASEPRITE_ANIMATION_DIRECTIONS = ["forward", "reverse", "pingpong"]
ASEPRITE_EXPORT_VERSION = "aseprite-atlas-v1"

SIDE_WORDS = {
    "left": ["left", "l", "lt", "lhs", "izq", "izquierda"],
    "right": ["right", "r", "rt", "rhs", "der", "derecha"],
}

ROLE_PATTERNS = [
    ("clothing", ["topwear", "clothes", "clothing", "shirt", "jacket", "dress", "ropa", "camisa"]),
    ("handwear", ["handwear", "glove", "gloves", "mitten", "mittens", "guante"]),
    ("hand", ["hand", "mano"]),
    ("forearm", ["forearm", "lowerarm", "lower_arm", "antebrazo"]),
    ("upper_arm", ["upperarm", "upper_arm", "armupper", "brazoalto"]),
    ("arm", ["arm", "brazo"]),
    ("foot", ["foot", "feet", "pie", "shoe", "boot"]),
    ("calf", ["calf", "shin", "lowerleg", "lower_leg", "pantorrilla"]),
    ("thigh", ["thigh", "upperleg", "upper_leg", "muslo"]),
    ("leg", ["leg", "pierna"]),
    ("head", ["head", "cabeza", "face", "cara"]),
    ("neck", ["neck", "cuello"]),
    ("ear", ["ear", "ears", "oreja"]),
    ("hair", ["hair", "pelo", "cabello"]),
    ("eyewhite", ["eyewhite", "eye_white", "whiteeye", "white_eye"]),
    ("iris", ["iris", "irides"]),
    ("eyelash", ["eyelash", "lashes", "pestaña"]),
    ("eyebrow", ["eyebrow", "brow", "ceja"]),
    ("eye", ["eye", "ojo"]),
    ("nose", ["nose", "nariz"]),
    ("mouth", ["mouth", "boca"]),
    ("torso", ["torso", "body", "chest", "spine", "cuerpo", "pecho"]),
    ("pelvis", ["pelvis", "hips", "hip", "cadera"]),
    ("weapon", ["weapon", "sword", "gun", "arma", "espada"]),
    ("accessory", ["accessory", "prop", "item", "accesorio"]),
]


def _safe_svg_id(value):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value)).strip("-")
    if not text:
        text = "part"
    if not re.match(r"^[a-zA-Z_]", text):
        text = f"part-{text}"
    return text


def _format_number(value):
    value = float(value)
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _safe_filename_stem(value, fallback="sprite"):
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "")).strip("_.-")
    return text or fallback


def _parse_sprite_names(sprite_names, sprite_count):
    if isinstance(sprite_names, (list, tuple)):
        names = [str(name).strip() for name in sprite_names]
    else:
        text = str(sprite_names or "").strip()
        names = []
        if text:
            try:
                loaded = json.loads(text)
                if isinstance(loaded, list):
                    names = [str(name).strip() for name in loaded]
                elif isinstance(loaded, dict):
                    names = [str(loaded.get(str(index + 1), "")).strip() for index in range(sprite_count)]
            except json.JSONDecodeError:
                names = [line.strip() for line in text.splitlines()]

    normalized = []
    used = set()
    for index in range(sprite_count):
        name = names[index] if index < len(names) and names[index] else f"sprite_{index + 1:02d}"
        base = _safe_filename_stem(name, f"sprite_{index + 1:02d}")
        unique = base
        suffix = 2
        while unique in used:
            unique = f"{base}_{suffix}"
            suffix += 1
        used.add(unique)
        normalized.append(unique)
    return normalized


def _build_aseprite_atlas(
    sheet_width,
    sheet_height,
    spritesheet_type,
    sprite_count,
    layout_direction,
    sprite_names,
    columns,
    image_filename,
    frame_duration,
):
    sheet_width = int(sheet_width)
    sheet_height = int(sheet_height)
    sprite_count = int(sprite_count)
    columns = int(columns)

    if sheet_width <= 0 or sheet_height <= 0:
        raise ValueError("Spritesheet width and height must be greater than zero")
    if sprite_count <= 0:
        raise ValueError("Sprite count must be greater than zero")

    layout_direction = str(layout_direction or "Horizontal")
    if layout_direction == "Horizontal":
        columns = sprite_count
    elif layout_direction == "Vertical":
        columns = 1
    elif columns <= 0:
        columns = sprite_count

    if columns > sprite_count:
        columns = sprite_count

    rows = int(math.ceil(sprite_count / columns))
    if sheet_width % columns != 0:
        raise ValueError(f"Spritesheet width {sheet_width} is not divisible by columns {columns}")
    if sheet_height % rows != 0:
        raise ValueError(f"Spritesheet height {sheet_height} is not divisible by calculated rows {rows}")

    frame_width = sheet_width // columns
    frame_height = sheet_height // rows
    names = _parse_sprite_names(sprite_names, sprite_count)
    frames = {}

    for index, name in enumerate(names):
        col = index % columns
        row = index // columns
        frame_key = f"{name}.png"
        frames[frame_key] = {
            "frame": {"x": col * frame_width, "y": row * frame_height, "w": frame_width, "h": frame_height},
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": frame_width, "h": frame_height},
            "sourceSize": {"w": frame_width, "h": frame_height},
            "duration": int(frame_duration),
        }

    atlas = {
        "frames": frames,
        "meta": {
            "app": "ComfyUI Game Assets Maker",
            "version": ASEPRITE_EXPORT_VERSION,
            "image": str(image_filename or "spritesheet.png"),
            "format": "RGBA8888",
            "size": {"w": sheet_width, "h": sheet_height},
            "scale": "1",
            "spritesheetType": spritesheet_type,
            "layout": {
                "direction": layout_direction,
                "columns": columns,
                "rows": rows,
                "spriteCount": sprite_count,
                "frameWidth": frame_width,
                "frameHeight": frame_height,
            },
        },
    }
    return atlas


def _load_aseprite_atlas(aseprite_json):
    if isinstance(aseprite_json, dict):
        atlas = deepcopy(aseprite_json)
    else:
        try:
            atlas = json.loads(str(aseprite_json or ""))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid Aseprite JSON: {error}") from error
    if not isinstance(atlas, dict):
        raise ValueError("Invalid Aseprite JSON: expected an object")
    return atlas


def _aseprite_frame_items(atlas):
    frames = atlas.get("frames")
    if isinstance(frames, dict):
        items = list(frames.items())
    elif isinstance(frames, list):
        items = []
        for index, frame_data in enumerate(frames):
            if not isinstance(frame_data, dict):
                continue
            filename = frame_data.get("filename") or f"sprite_{index + 1:02d}.png"
            items.append((str(filename), frame_data))
    else:
        raise ValueError("Invalid Aseprite JSON: missing frames object/list")

    parsed = []
    for filename, frame_data in items:
        if not isinstance(frame_data, dict) or not isinstance(frame_data.get("frame"), dict):
            continue
        frame = frame_data["frame"]
        try:
            x = int(frame["x"])
            y = int(frame["y"])
            w = int(frame["w"])
            h = int(frame["h"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid frame coordinates for {filename}") from error
        if w <= 0 or h <= 0:
            raise ValueError(f"Invalid frame size for {filename}: {w}x{h}")
        parsed.append({"name": str(filename), "x": x, "y": y, "w": w, "h": h})

    if not parsed:
        raise ValueError("Aseprite JSON does not contain any valid frames")
    return parsed


def _extract_aseprite_sprites(image, frame_items, image_index=0, background_value=0.0):
    if not isinstance(image, torch.Tensor) or image.ndim != 4:
        raise ValueError("Expected IMAGE tensor with shape [batch, height, width, channels]")

    batch_index = max(0, min(int(image_index), image.shape[0] - 1))
    sheet = image[batch_index]
    sheet_h, sheet_w = int(sheet.shape[0]), int(sheet.shape[1])
    channels = int(sheet.shape[2])

    max_w = max(item["w"] for item in frame_items)
    max_h = max(item["h"] for item in frame_items)
    sprites = []
    clipped = []

    for item in frame_items:
        x1 = max(0, item["x"])
        y1 = max(0, item["y"])
        x2 = min(sheet_w, item["x"] + item["w"])
        y2 = min(sheet_h, item["y"] + item["h"])
        if x1 >= x2 or y1 >= y2:
            raise ValueError(f"Frame {item['name']} is outside the spritesheet bounds")

        crop = sheet[y1:y2, x1:x2, :]
        canvas = torch.full(
            (max_h, max_w, channels),
            float(background_value),
            dtype=sheet.dtype,
            device=sheet.device,
        )
        canvas[: crop.shape[0], : crop.shape[1], :] = crop
        sprites.append(canvas)

        if x1 != item["x"] or y1 != item["y"] or x2 != item["x"] + item["w"] or y2 != item["y"] + item["h"]:
            clipped.append(item["name"])

    return torch.stack(sprites, dim=0), clipped


def _parse_aseprite_animation_tags(animation_tags, animation_count, frame_count):
    if isinstance(animation_tags, (list, tuple)):
        raw_tags = list(animation_tags)
    else:
        text = str(animation_tags or "").strip()
        raw_tags = []
        if text:
            try:
                loaded = json.loads(text)
                if isinstance(loaded, list):
                    raw_tags = loaded
            except json.JSONDecodeError:
                raw_tags = []

    frame_count = max(1, int(frame_count))
    animation_count = max(1, int(animation_count))
    tags = []
    for index in range(animation_count):
        raw = raw_tags[index] if index < len(raw_tags) and isinstance(raw_tags[index], dict) else {}
        name = _safe_filename_stem(raw.get("name") or f"animation_{index + 1:02d}", f"animation_{index + 1:02d}")
        from_frame = int(raw.get("from", 0))
        to_frame = int(raw.get("to", frame_count - 1))
        direction = str(raw.get("direction") or "forward").lower()
        color = str(raw.get("color") or "#000000ff")

        if direction not in ASEPRITE_ANIMATION_DIRECTIONS:
            direction = "forward"
        from_frame = max(0, min(frame_count - 1, from_frame))
        to_frame = max(0, min(frame_count - 1, to_frame))
        if from_frame > to_frame:
            from_frame, to_frame = to_frame, from_frame
        if not re.match(r"^#[0-9a-fA-F]{8}$", color):
            color = "#000000ff"

        tags.append(
            {
                "name": name,
                "from": from_frame,
                "to": to_frame,
                "direction": direction,
                "color": color.lower(),
            }
        )
    return tags


def _build_aseprite_animation_json(aseprite_json, frame_count, animation_count, animation_tags):
    if str(aseprite_json or "").strip():
        output = _load_aseprite_atlas(aseprite_json)
        try:
            inferred_frame_count = len(_aseprite_frame_items(output))
        except ValueError:
            inferred_frame_count = int(frame_count)
        frame_count = inferred_frame_count or int(frame_count)
    else:
        output = {"meta": {}}

    tags = _parse_aseprite_animation_tags(animation_tags, animation_count, frame_count)
    meta = output.setdefault("meta", {})
    meta["frameTags"] = tags
    return output, tags


def _aseprite_animation_frame_names(frame_count, tags, fallback_prefix):
    frame_count = max(1, int(frame_count))
    counters = {}
    names = []
    for frame_index in range(frame_count):
        owner = None
        for tag in tags:
            if int(tag["from"]) <= frame_index <= int(tag["to"]):
                owner = tag["name"]
                break
        base = _safe_filename_stem(owner or fallback_prefix or "frame", "frame")
        counters[base] = counters.get(base, 0) + 1
        names.append(f"{base}_{counters[base]:02d}")
    return names


def _build_aseprite_animation_atlas(
    sheet_width,
    sheet_height,
    frame_count,
    layout_direction,
    columns,
    animation_count,
    animation_tags,
    image_filename,
    frame_duration,
    frame_name_prefix,
):
    tags = _parse_aseprite_animation_tags(animation_tags, animation_count, frame_count)
    frame_names = _aseprite_animation_frame_names(frame_count, tags, frame_name_prefix)
    atlas = _build_aseprite_atlas(
        sheet_width=sheet_width,
        sheet_height=sheet_height,
        spritesheet_type="Animation",
        sprite_count=frame_count,
        layout_direction=layout_direction,
        sprite_names=frame_names,
        columns=columns,
        image_filename=image_filename,
        frame_duration=frame_duration,
    )
    atlas["meta"]["frameTags"] = tags
    atlas["meta"]["atlasType"] = "Animation"
    return atlas, tags, frame_names


def _aseprite_frame_tags(atlas):
    tags = atlas.get("meta", {}).get("frameTags", [])
    if not isinstance(tags, list):
        return []
    normalized = []
    for index, tag in enumerate(tags):
        if not isinstance(tag, dict):
            continue
        try:
            from_frame = int(tag.get("from", 0))
            to_frame = int(tag.get("to", from_frame))
        except (TypeError, ValueError):
            continue
        direction = str(tag.get("direction") or "forward").lower()
        if direction not in ASEPRITE_ANIMATION_DIRECTIONS:
            direction = "forward"
        normalized.append(
            {
                "name": str(tag.get("name") or f"animation_{index + 1:02d}"),
                "from": from_frame,
                "to": to_frame,
                "direction": direction,
                "color": str(tag.get("color") or "#000000ff"),
            }
        )
    return normalized


def _animation_frame_indexes(tag, frame_count, loop_count=1, include_pingpong_endpoint=False):
    if frame_count <= 0:
        return []
    start = max(0, min(frame_count - 1, int(tag.get("from", 0))))
    end = max(0, min(frame_count - 1, int(tag.get("to", start))))
    if start > end:
        start, end = end, start

    indexes = list(range(start, end + 1))
    direction = str(tag.get("direction") or "forward").lower()
    if direction == "reverse":
        indexes = list(reversed(indexes))
    elif direction == "pingpong" and len(indexes) > 1:
        reverse_tail = list(reversed(indexes if include_pingpong_endpoint else indexes[1:-1]))
        indexes = indexes + reverse_tail

    loop_count = max(1, int(loop_count))
    return indexes * loop_count


def _contour_to_path(contour, offset_x, offset_y, close_path=True):
    points = contour.reshape(-1, 2)
    if len(points) < 2:
        return ""

    commands = [f"M {_format_number(points[0][0] + offset_x)} {_format_number(points[0][1] + offset_y)}"]
    for point in points[1:]:
        commands.append(f"L {_format_number(point[0] + offset_x)} {_format_number(point[1] + offset_y)}")
    if close_path:
        commands.append("Z")
    return " ".join(commands)


def _sort_parts(tag2pinfo, depth_order):
    items = list(tag2pinfo.items())
    if depth_order == "input_order":
        return items
    reverse = depth_order == "back_to_front"
    return sorted(items, key=lambda item: item[1].get("depth_median", 1.0), reverse=reverse)


def _svg_attributes(**values):
    attrs = []
    for key, value in values.items():
        if value is None:
            continue
        attr_name = key.replace("_", "-")
        attr_value = escape(str(value), {'"': "&quot;"})
        attrs.append(f'{attr_name}="{attr_value}"')
    return " ".join(attrs)


def _tag_tokens(tag):
    text = str(tag).lower()
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(tag)).lower()
    return [token for token in re.split(r"[^a-z0-9]+", spaced) if token], text.replace("-", "_")


def _detect_side(tag):
    tokens, compact = _tag_tokens(tag)
    token_set = set(tokens)
    for side, words in SIDE_WORDS.items():
        if token_set.intersection(words):
            return side
    if compact.startswith(("l_", "left_")) or compact.endswith(("_l", "_left")):
        return "left"
    if compact.startswith(("r_", "right_")) or compact.endswith(("_r", "_right")):
        return "right"
    return "center"


def _detect_role(tag):
    tokens, compact = _tag_tokens(tag)
    token_set = set(tokens)
    for role, patterns in ROLE_PATTERNS:
        for pattern in patterns:
            if pattern in token_set or pattern in compact:
                return role
    return "unknown"


def _part_bounds(pinfo, alpha_threshold):
    img = pinfo.get("img")
    xyxy = pinfo.get("xyxy", [0, 0, 0, 0])
    offset_x, offset_y = int(xyxy[0]), int(xyxy[1])

    if isinstance(img, np.ndarray) and img.ndim == 3 and img.shape[-1] >= 4:
        alpha = img[..., 3]
        ys, xs = np.where(alpha > int(alpha_threshold))
        if xs.size and ys.size:
            x1 = int(xs.min()) + offset_x
            y1 = int(ys.min()) + offset_y
            x2 = int(xs.max()) + offset_x + 1
            y2 = int(ys.max()) + offset_y + 1
            centroid = [float(xs.mean() + offset_x), float(ys.mean() + offset_y)]
            return [x1, y1, x2, y2], centroid

    if len(xyxy) >= 4:
        x1, y1, x2, y2 = [int(value) for value in xyxy[:4]]
    else:
        x1 = y1 = x2 = y2 = 0
    return [x1, y1, x2, y2], [(x1 + x2) / 2.0, (y1 + y2) / 2.0]


def _pivot_for_role(role, bounds, centroid):
    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    if role == "head":
        return [cx, y2]
    if role == "hair":
        return centroid
    if role == "neck":
        return [cx, y1]
    if role in {"hand", "forearm", "upper_arm", "arm", "thigh", "calf", "leg", "foot"}:
        return [cx, y1]
    if role in {"ear", "eyewhite", "iris", "eyelash", "eyebrow", "eye", "nose", "mouth", "handwear", "weapon", "accessory"}:
        return centroid
    return [cx, cy]


def _bone_tail_for_role(role, bounds, pivot):
    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    if role in {"head", "hair", "ear"}:
        return [cx, y1]
    if role in {"neck", "torso", "pelvis", "clothing"}:
        return [cx, y2]
    if role in {"hand", "forearm", "upper_arm", "arm", "thigh", "calf", "leg", "foot"}:
        return [cx, y2]
    return [cx, cy]


def _find_part_id(parts, role, side=None):
    for part in parts:
        if part["role"] != role:
            continue
        if side is None or part["side"] == side or part["side"] == "center":
            return part["id"]
    return None


def _suggest_parent(part, parts):
    role = part["role"]
    side = part["side"]
    if role == "head":
        return _find_part_id(parts, "neck") or _find_part_id(parts, "torso")
    if role in {"ear", "hair", "eyewhite", "eyebrow", "eye", "nose", "mouth"}:
        return _find_part_id(parts, "head")
    if role in {"iris", "eyelash"}:
        return _find_part_id(parts, "eyewhite", side) or _find_part_id(parts, "eye", side) or _find_part_id(parts, "head")
    if role == "neck":
        return _find_part_id(parts, "torso") or _find_part_id(parts, "clothing")
    if role == "clothing":
        return _find_part_id(parts, "torso")
    if role == "handwear":
        return _find_part_id(parts, "hand", side) or _find_part_id(parts, "clothing") or _find_part_id(parts, "torso")
    if role == "pelvis":
        return _find_part_id(parts, "torso")
    if role == "upper_arm":
        return _find_part_id(parts, "torso")
    if role == "forearm":
        return _find_part_id(parts, "upper_arm", side) or _find_part_id(parts, "arm", side)
    if role == "hand":
        return _find_part_id(parts, "forearm", side) or _find_part_id(parts, "arm", side)
    if role == "arm":
        return _find_part_id(parts, "torso")
    if role == "thigh":
        return _find_part_id(parts, "pelvis") or _find_part_id(parts, "torso")
    if role == "calf":
        return _find_part_id(parts, "thigh", side) or _find_part_id(parts, "leg", side)
    if role == "foot":
        return _find_part_id(parts, "calf", side) or _find_part_id(parts, "leg", side)
    if role == "leg":
        return _find_part_id(parts, "pelvis") or _find_part_id(parts, "torso")
    if role in {"weapon", "accessory"}:
        return _find_part_id(parts, "hand", side) or _find_part_id(parts, "torso")
    return None


def _round_point(point):
    return [round(float(point[0]), 3), round(float(point[1]), 3)]


def _load_rig(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Expected GAME_ASSET_RIG dict or rig JSON string")
    return deepcopy(value)


def _load_overrides(value):
    text = (value or "").strip()
    if not text:
        return {}
    overrides = json.loads(text)
    if not isinstance(overrides, dict):
        raise ValueError("Rig overrides must be a JSON object")
    return overrides


def _override_items(value, key_name):
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, dict):
                continue
            item_id = item.get(key_name) or item.get("id") or item.get("tag")
            if item_id:
                data = dict(item)
                data.pop(key_name, None)
                items.append((str(item_id), data))
        return items
    raise ValueError("Override sections must be objects or lists")


def _find_index_by_id_or_tag(items, selector):
    if selector.startswith("tag:"):
        tag = selector[4:]
        for index, item in enumerate(items):
            if item.get("tag") == tag:
                return index
        return None

    for index, item in enumerate(items):
        if item.get("id") == selector or item.get("tag") == selector:
            return index
    return None


def _merge_override(target, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_override(target[key], value)
        else:
            target[key] = value


def _apply_rig_overrides(rig, overrides):
    changed_parts = []
    changed_bones = []

    parts = rig.setdefault("parts", [])
    for selector, override in _override_items(overrides.get("parts"), "part"):
        index = _find_index_by_id_or_tag(parts, str(selector))
        if index is None:
            raise ValueError(f"Unknown rig part override target: {selector}")
        _merge_override(parts[index], override)
        changed_parts.append(parts[index].get("id", str(selector)))

    bones = rig.setdefault("bones", [])
    for selector, override in _override_items(overrides.get("bones"), "bone"):
        index = _find_index_by_id_or_tag(bones, str(selector))
        if index is None:
            raise ValueError(f"Unknown rig bone override target: {selector}")
        _merge_override(bones[index], override)
        changed_bones.append(bones[index].get("id", str(selector)))

    rig["overrides"] = {
        "parts": changed_parts,
        "bones": changed_bones,
    }
    return changed_parts, changed_bones


def _rig_report(rig, title="Rig"):
    stats = rig.get("stats", {})
    canvas = rig.get("canvas", {})
    parts = rig.get("parts", [])
    bones = rig.get("bones", [])
    lines = [
        title,
        f'canvas: {canvas.get("width", "?")}x{canvas.get("height", "?")}',
        f'parts: {stats.get("parts", len(parts))} | bones: {len(bones)}',
        "",
    ]
    for part in parts:
        bone = next((item for item in bones if item.get("part") == part.get("id")), {})
        parent = bone.get("parent") or "root"
        lines.append(
            f'- {part.get("tag", part.get("id"))}: role={part.get("role")}, side={part.get("side")}, '
            f'pivot={part.get("pivot")}, parent={parent}'
        )
    return "\n".join(lines)


def _rig_to_svg(rig, show_bounds, show_labels, bone_color, pivot_color, label_color, stroke_width):
    canvas = rig.get("canvas", {})
    width = int(canvas.get("width", 1024))
    height = int(canvas.get("height", 1024))
    parts = rig.get("parts", [])
    bones = rig.get("bones", [])

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "  <title>Game asset rig preview</title>",
        '  <g id="rig-bounds" fill="none">',
    ]

    if show_bounds:
        for part in parts:
            bounds = part.get("bounds") or [0, 0, 0, 0]
            x1, y1, x2, y2 = [float(value) for value in bounds[:4]]
            attrs = _svg_attributes(
                x=_format_number(x1),
                y=_format_number(y1),
                width=_format_number(max(0.0, x2 - x1)),
                height=_format_number(max(0.0, y2 - y1)),
                stroke="#999999",
                stroke_width="1",
                stroke_dasharray="6 4",
                data_part=part.get("id", ""),
            )
            lines.append(f"    <rect {attrs}/>")
    lines.append("  </g>")

    lines.append('  <g id="rig-bones" fill="none">')
    for bone in bones:
        head = bone.get("head") or [0, 0]
        tail = bone.get("tail") or head
        attrs = _svg_attributes(
            x1=_format_number(head[0]),
            y1=_format_number(head[1]),
            x2=_format_number(tail[0]),
            y2=_format_number(tail[1]),
            stroke=bone_color,
            stroke_width=_format_number(stroke_width),
            stroke_linecap="round",
            data_bone=bone.get("id", ""),
            data_parent=bone.get("parent", "") or "",
        )
        lines.append(f"    <line {attrs}/>")
    lines.append("  </g>")

    lines.append('  <g id="rig-pivots">')
    for part in parts:
        pivot = part.get("pivot") or [0, 0]
        attrs = _svg_attributes(
            cx=_format_number(pivot[0]),
            cy=_format_number(pivot[1]),
            r="5",
            fill=pivot_color,
            stroke="#ffffff",
            stroke_width="2",
            data_part=part.get("id", ""),
        )
        lines.append(f"    <circle {attrs}/>")
        if show_labels:
            label_attrs = _svg_attributes(
                x=_format_number(pivot[0] + 8),
                y=_format_number(pivot[1] - 8),
                fill=label_color,
                font_size="18",
                font_family="monospace",
                data_part=part.get("id", ""),
            )
            lines.append(f"    <text {label_attrs}>{escape(str(part.get('tag', part.get('id', ''))))}</text>")
    lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines)


def _keypoints_to_array(keypoints, canvas_width, canvas_height):
    if keypoints is None:
        return np.zeros((0, 3), dtype=np.float32)
    array = np.asarray(keypoints, dtype=np.float32)
    if array.size == 0:
        return np.zeros((0, 3), dtype=np.float32)
    if array.ndim == 1:
        usable = array.size - (array.size % 3)
        array = array[:usable].reshape((-1, 3))
    elif array.ndim == 2 and array.shape[1] >= 3:
        array = array[:, :3]
    else:
        return np.zeros((0, 3), dtype=np.float32)

    if array.size and np.nanmax(np.abs(array[:, :2])) <= 1.0:
        array[:, 0] *= float(canvas_width)
        array[:, 1] *= float(canvas_height)
    return array


def _pose_person(pose_kps, person_index):
    if not isinstance(pose_kps, list) or not pose_kps:
        return None, 0, 0
    frame = pose_kps[0]
    if not isinstance(frame, dict):
        return None, 0, 0
    people = frame.get("people") or []
    if person_index < 0 or person_index >= len(people):
        return None, int(frame.get("canvas_width", 0)), int(frame.get("canvas_height", 0))
    return people[person_index], int(frame.get("canvas_width", 0)), int(frame.get("canvas_height", 0))


def _valid_keypoint(array, index, min_confidence):
    if index < 0 or index >= len(array):
        return None
    point = array[index]
    if len(point) < 3 or float(point[2]) < float(min_confidence):
        return None
    if not np.isfinite(point[:2]).all():
        return None
    return [float(point[0]), float(point[1])]


def _mean_keypoint(array, indexes, min_confidence):
    points = [_valid_keypoint(array, index, min_confidence) for index in indexes]
    points = [point for point in points if point is not None]
    if not points:
        return None
    return [float(np.mean([point[0] for point in points])), float(np.mean([point[1] for point in points]))]


def _pose_points_from_person(person, canvas_width, canvas_height, min_confidence):
    if not isinstance(person, dict):
        return {}

    body = _keypoints_to_array(person.get("pose_keypoints_2d"), canvas_width, canvas_height)
    face = _keypoints_to_array(person.get("face_keypoints_2d"), canvas_width, canvas_height)
    left_hand = _keypoints_to_array(person.get("hand_left_keypoints_2d"), canvas_width, canvas_height)
    right_hand = _keypoints_to_array(person.get("hand_right_keypoints_2d"), canvas_width, canvas_height)

    points = {
        "nose": _valid_keypoint(body, 0, min_confidence) or _mean_keypoint(face, range(27, 36), min_confidence),
        "neck": _valid_keypoint(body, 1, min_confidence),
        "right_shoulder": _valid_keypoint(body, 2, min_confidence),
        "right_elbow": _valid_keypoint(body, 3, min_confidence),
        "right_wrist": _valid_keypoint(body, 4, min_confidence),
        "left_shoulder": _valid_keypoint(body, 5, min_confidence),
        "left_elbow": _valid_keypoint(body, 6, min_confidence),
        "left_wrist": _valid_keypoint(body, 7, min_confidence),
        "right_hip": _valid_keypoint(body, 8, min_confidence),
        "right_knee": _valid_keypoint(body, 9, min_confidence),
        "right_ankle": _valid_keypoint(body, 10, min_confidence),
        "left_hip": _valid_keypoint(body, 11, min_confidence),
        "left_knee": _valid_keypoint(body, 12, min_confidence),
        "left_ankle": _valid_keypoint(body, 13, min_confidence),
        "right_eye": _valid_keypoint(body, 14, min_confidence) or _mean_keypoint(face, range(42, 48), min_confidence),
        "left_eye": _valid_keypoint(body, 15, min_confidence) or _mean_keypoint(face, range(36, 42), min_confidence),
        "mouth": _mean_keypoint(face, range(48, 68), min_confidence),
        "face": _mean_keypoint(face, range(0, 68), min_confidence),
        "left_hand": _mean_keypoint(left_hand, range(0, 21), min_confidence),
        "right_hand": _mean_keypoint(right_hand, range(0, 21), min_confidence),
    }
    return {key: _round_point(value) for key, value in points.items() if value is not None}


def _pose_source_for_part(part):
    role = part.get("role")
    side = part.get("side")
    if role == "head":
        return "neck"
    if role == "neck":
        return "neck"
    if role == "nose":
        return "nose"
    if role == "mouth":
        return "mouth"
    if role in {"eyewhite", "iris", "eyelash", "eyebrow", "eye"}:
        return f"{side}_eye" if side in {"left", "right"} else None
    if role == "upper_arm":
        return f"{side}_shoulder" if side in {"left", "right"} else None
    if role == "forearm":
        return f"{side}_elbow" if side in {"left", "right"} else None
    if role == "hand":
        return f"{side}_wrist" if side in {"left", "right"} else None
    if role == "thigh":
        return f"{side}_hip" if side in {"left", "right"} else None
    if role == "calf":
        return f"{side}_knee" if side in {"left", "right"} else None
    if role == "foot":
        return f"{side}_ankle" if side in {"left", "right"} else None
    return None


def _pose_tail_source_for_bone(bone):
    role = bone.get("role")
    side = bone.get("side")
    if role == "head":
        return "nose"
    if role == "neck":
        return "face"
    if role == "upper_arm":
        return f"{side}_elbow" if side in {"left", "right"} else None
    if role == "forearm":
        return f"{side}_wrist" if side in {"left", "right"} else None
    if role == "thigh":
        return f"{side}_knee" if side in {"left", "right"} else None
    if role == "calf":
        return f"{side}_ankle" if side in {"left", "right"} else None
    return None


def _apply_pose_points_to_rig(rig, pose_points):
    part_sources = {}
    bone_sources = {}

    for part in rig.get("parts", []):
        source = _pose_source_for_part(part)
        if source and source in pose_points:
            part["pivot"] = pose_points[source]
            part["pivot_source"] = f"pose:{source}"
            part_sources[part["id"]] = source
        else:
            part["pivot_source"] = "fallback:auto"

    parts_by_id = {part.get("id"): part for part in rig.get("parts", [])}
    for bone in rig.get("bones", []):
        part = parts_by_id.get(bone.get("part"))
        if part is not None:
            bone["head"] = part.get("pivot", bone.get("head"))
        source = _pose_tail_source_for_bone(bone)
        if source and source in pose_points:
            bone["tail"] = pose_points[source]
            bone["tail_source"] = f"pose:{source}"
            bone_sources[bone["id"]] = source
        else:
            bone["tail_source"] = "fallback:auto"

    rig["pose"] = {
        "points": pose_points,
        "parts_from_pose": part_sources,
        "bones_from_pose": bone_sources,
    }
    return part_sources, bone_sources


def _spine_y(value, canvas_height, scale):
    return (float(canvas_height) - float(value)) * float(scale)


def _spine_point(point, canvas_height, scale):
    return [float(point[0]) * float(scale), _spine_y(point[1], canvas_height, scale)]


def _part_image_info(part, tag2pinfo):
    pinfo = tag2pinfo.get(part.get("tag")) or tag2pinfo.get(part.get("id"))
    if not isinstance(pinfo, dict):
        return None, [0, 0, 0, 0]
    img = pinfo.get("img")
    xyxy = pinfo.get("xyxy") or part.get("bounds") or [0, 0, 0, 0]
    return img, [float(value) for value in xyxy[:4]]


def _write_part_png(img, output_path):
    if not isinstance(img, np.ndarray) or img.ndim != 3:
        return False
    image = img
    if image.dtype != np.uint8:
        image = np.clip(image, 0.0, 1.0) * 255.0 if np.issubdtype(image.dtype, np.floating) else np.clip(image, 0, 255)
        image = image.astype(np.uint8)
    if image.shape[-1] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGRA)
    elif image.shape[-1] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    else:
        return False
    return bool(cv2.imwrite(output_path, image))


def _spine_bone_name(part_id):
    return f"{part_id}_bone"


def _rotate_point(point, degrees):
    radians = math.radians(float(degrees))
    cos_value = math.cos(radians)
    sin_value = math.sin(radians)
    x, y = float(point[0]), float(point[1])
    return [x * cos_value - y * sin_value, x * sin_value + y * cos_value]


def _to_local_point(point, origin, rotation_degrees):
    return _rotate_point([float(point[0]) - float(origin[0]), float(point[1]) - float(origin[1])], -float(rotation_degrees))


def _spine_bone_shape(bone, absolute, parent_rotation, canvas_height, scale):
    if bone.get("role") in SPINE_POINT_CONTROL_ROLES:
        return None, float(parent_rotation)

    tail = bone.get("tail") or bone.get("head")
    tail_absolute = _spine_point(tail, canvas_height, scale)
    dx = tail_absolute[0] - absolute[0]
    dy = tail_absolute[1] - absolute[1]
    length = math.hypot(dx, dy)
    if length <= 0.001:
        return None, parent_rotation

    absolute_rotation = math.degrees(math.atan2(dy, dx))
    local_rotation = absolute_rotation - float(parent_rotation)
    return {
        "rotation": round(local_rotation, 3),
        "length": round(length, 3),
    }, absolute_rotation


def _spine_setup_bones(spine_bones):
    return {
        bone.get("name"): {
            "x": float(bone.get("x", 0.0)),
            "y": float(bone.get("y", 0.0)),
            "rotation": float(bone.get("rotation", 0.0)),
        }
        for bone in spine_bones
    }


def _rig_bones_by_role(rig, roles=None):
    requested = set(roles) if roles is not None else None
    return [
        bone
        for bone in rig.get("bones", [])
        if requested is None or bone.get("role") in requested
    ]


def _add_timeline(animation, bone_name, timeline_name, frames):
    if not frames:
        return
    timelines = animation.setdefault("bones", {}).setdefault(bone_name, {})
    timelines.setdefault(timeline_name, []).extend(frames)


def _translate_frames(setup_bones, bone_name, points):
    setup = setup_bones.get(bone_name, {"x": 0.0, "y": 0.0})
    return [
        {"time": round(float(time), 3), "x": round(setup["x"] + float(dx), 3), "y": round(setup["y"] + float(dy), 3)}
        for time, dx, dy in points
    ]


def _rotate_frames(points):
    return [{"time": round(float(time), 3), "value": round(float(value), 3)} for time, value in points]


def _scale_frames(points):
    return [{"time": round(float(time), 3), "x": round(float(x), 3), "y": round(float(y), 3)} for time, x, y in points]


def _merge_spine_animation(base, extra):
    for bone_name, timelines in extra.get("bones", {}).items():
        target = base.setdefault("bones", {}).setdefault(bone_name, {})
        for timeline_name, frames in timelines.items():
            target.setdefault(timeline_name, []).extend(frames)
            target[timeline_name].sort(key=lambda frame: float(frame.get("time", 0.0)))
    return base


def _build_idle_breath_animation(rig, spine_bones, duration, intensity):
    duration = max(0.25, float(duration))
    strength = max(0.0, float(intensity))
    setup_bones = _spine_setup_bones(spine_bones)
    animation = {"bones": {}}
    amplitudes = {
        "torso": 8.0,
        "clothing": 8.0,
        "neck": 4.0,
        "head": 3.0,
    }
    for bone in _rig_bones_by_role(rig, amplitudes.keys()):
        bone_name = bone.get("id")
        if bone_name not in setup_bones:
            continue
        amplitude = amplitudes.get(bone.get("role"), 3.0) * strength
        _add_timeline(
            animation,
            bone_name,
            "translate",
            _translate_frames(setup_bones, bone_name, [(0.0, 0.0, 0.0), (duration / 2.0, 0.0, amplitude), (duration, 0.0, 0.0)]),
        )
    return animation


def _build_blink_animation(rig, spine_bones, duration, intensity, blink_interval):
    duration = max(0.25, float(duration))
    strength = max(0.0, min(1.0, float(intensity)))
    interval = max(0.25, float(blink_interval))
    closed_y = max(0.05, 1.0 - (0.9 * strength))
    animation = {"bones": {}}
    eye_roles = {"eyewhite", "iris", "eyelash", "eye"}
    eye_bones = [bone.get("id") for bone in _rig_bones_by_role(rig, eye_roles)]
    eye_bones = [bone_name for bone_name in eye_bones if any(item.get("name") == bone_name for item in spine_bones)]
    if not eye_bones:
        return animation

    blink_times = []
    current = min(0.45, duration / 2.0)
    while current < duration:
        blink_times.append(current)
        current += interval
    if not blink_times:
        blink_times.append(duration / 2.0)

    for bone_name in eye_bones:
        points = [(0.0, 1.0, 1.0)]
        for time in blink_times:
            close_time = min(duration, time + 0.045)
            open_time = min(duration, time + 0.13)
            points.extend([(time, 1.0, 1.0), (close_time, 1.0, closed_y), (open_time, 1.0, 1.0)])
        points.append((duration, 1.0, 1.0))
        _add_timeline(animation, bone_name, "scale", _scale_frames(points))
    return animation


def _build_hair_sway_animation(rig, spine_bones, duration, intensity):
    duration = max(0.25, float(duration))
    strength = max(0.0, float(intensity))
    setup_bones = _spine_setup_bones(spine_bones)
    animation = {"bones": {}}
    for index, bone in enumerate(_rig_bones_by_role(rig, {"hair"})):
        bone_name = bone.get("id")
        if bone_name not in setup_bones:
            continue
        base_rotation = setup_bones[bone_name]["rotation"]
        amplitude = (4.0 + (index % 3)) * strength
        if "back" in str(bone.get("part", "")).lower():
            amplitude *= -0.8
        _add_timeline(
            animation,
            bone_name,
            "rotate",
            _rotate_frames(
                [
                    (0.0, base_rotation),
                    (duration * 0.25, base_rotation + amplitude),
                    (duration * 0.75, base_rotation - amplitude),
                    (duration, base_rotation),
                ]
            ),
        )
    return animation


def _build_face_alive_animation(rig, spine_bones, duration, intensity):
    duration = max(0.25, float(duration))
    strength = max(0.0, float(intensity))
    setup_bones = _spine_setup_bones(spine_bones)
    animation = {"bones": {}}
    for bone in _rig_bones_by_role(rig, {"iris"}):
        bone_name = bone.get("id")
        if bone_name not in setup_bones:
            continue
        direction = -1.0 if bone.get("side") == "right" else 1.0
        amplitude = 3.0 * strength * direction
        _add_timeline(
            animation,
            bone_name,
            "translate",
            _translate_frames(
                setup_bones,
                bone_name,
                [(0.0, 0.0, 0.0), (duration * 0.35, amplitude, 0.8 * strength), (duration * 0.7, -amplitude, 0.0), (duration, 0.0, 0.0)],
            ),
        )

    for bone in _rig_bones_by_role(rig, {"mouth"}):
        bone_name = bone.get("id")
        if bone_name not in setup_bones:
            continue
        amount = 0.04 * strength
        _add_timeline(
            animation,
            bone_name,
            "scale",
            _scale_frames([(0.0, 1.0, 1.0), (duration / 2.0, 1.0 + amount, 1.0 - amount), (duration, 1.0, 1.0)]),
        )
    return animation


def _build_spine_animations(rig, spine_bones, preset, duration, intensity, blink_interval):
    if preset in {None, "", "none"}:
        preset = "all"
    preset = preset if preset in SPINE_ANIMATION_PRESETS else "all"
    if preset == "no_animations":
        return {}

    builders = {
        "idle_breath": lambda: _build_idle_breath_animation(rig, spine_bones, duration, intensity),
        "blink": lambda: _build_blink_animation(rig, spine_bones, duration, intensity, blink_interval),
        "hair_sway": lambda: _build_hair_sway_animation(rig, spine_bones, duration, intensity),
        "face_alive": lambda: _build_face_alive_animation(rig, spine_bones, duration, intensity),
    }
    animations = {}
    if preset in builders:
        animations[preset] = builders[preset]()
        return animations

    idle = {"bones": {}}
    for name, builder in builders.items():
        built = builder()
        if preset == "all":
            animations[name] = built
        _merge_spine_animation(idle, built)
    animations["idle"] = idle
    return animations


def _spine_control_bone_id(part_id):
    return _spine_bone_name(part_id) if part_id else None


def _spine_export_bone_ids(rig):
    export_ids = {"root"}
    for bone in rig.get("bones", []):
        if bone.get("role") in SPINE_CONTROL_BONE_ROLES:
            export_ids.add(bone.get("id") or _spine_bone_name(bone.get("part", "part")))
    return export_ids


def _rig_bones_by_id(rig):
    return {
        bone.get("id") or _spine_bone_name(bone.get("part", "part")): bone
        for bone in rig.get("bones", [])
    }


def _nearest_exported_parent_bone(bone, bones_by_id, export_ids):
    parent = bone.get("parent") or "root"
    visited = set()
    while parent and parent != "root" and parent not in visited:
        if parent in export_ids:
            return parent
        visited.add(parent)
        parent_bone = bones_by_id.get(parent)
        parent = parent_bone.get("parent") if parent_bone else "root"
    return "root"


def _part_role_by_id(rig):
    return {part.get("id"): part.get("role") for part in rig.get("parts", [])}


def _attachment_bone_for_part(part, rig, bones_by_part, bones_by_id, export_ids):
    part_id = part.get("id")
    candidate = _spine_control_bone_id(part_id)
    if candidate in export_ids:
        return candidate

    role = part.get("role")
    side = part.get("side")
    parts = rig.get("parts", [])
    fallback_part_id = None
    if role in {"eyewhite", "eyebrow", "eye", "nose", "mouth", "ear"}:
        fallback_part_id = _find_part_id(parts, "head")
    elif role in {"neck", "handwear", "hand", "forearm", "upper_arm", "arm", "accessory"}:
        fallback_part_id = _find_part_id(parts, "clothing") or _find_part_id(parts, "torso")
    elif role in {"leg", "foot", "thigh", "calf"}:
        return "root"
    elif role in {"weapon"}:
        fallback_part_id = _find_part_id(parts, "hand", side) or _find_part_id(parts, "clothing")

    fallback_bone = _spine_control_bone_id(fallback_part_id)
    if fallback_bone in export_ids:
        return fallback_bone

    source_bone = bones_by_part.get(part_id)
    if source_bone:
        parent = _nearest_exported_parent_bone(source_bone, bones_by_id, export_ids)
        if parent in export_ids:
            return parent
    return "root"


def _build_spine_bones(rig, canvas_height, scale):
    bones = [{"name": "root"}]
    bone_abs = {"root": [0.0, 0.0]}
    bone_rotation_abs = {"root": 0.0}
    export_ids = _spine_export_bone_ids(rig)
    bones_by_id = _rig_bones_by_id(rig)
    rig_bones = [bone for bone in rig.get("bones", []) if (bone.get("id") or _spine_bone_name(bone.get("part", "part"))) in export_ids]
    pending = list(rig_bones)

    while pending:
        progressed = False
        next_pending = []
        for bone in pending:
            name = bone.get("id") or _spine_bone_name(bone.get("part", "part"))
            parent = _nearest_exported_parent_bone(bone, bones_by_id, export_ids)
            if parent not in bone_abs:
                next_pending.append(bone)
                continue
            head = bone.get("head") or [0, canvas_height]
            absolute = _spine_point(head, canvas_height, scale)
            parent_absolute = bone_abs[parent]
            parent_rotation = bone_rotation_abs.get(parent, 0.0)
            shape, absolute_rotation = _spine_bone_shape(
                bone,
                absolute,
                parent_rotation,
                canvas_height,
                scale,
            )
            local_position = _to_local_point(absolute, parent_absolute, parent_rotation)
            entry = {
                "name": name,
                "parent": parent,
                "x": round(local_position[0], 3),
                "y": round(local_position[1], 3),
            }
            if shape:
                entry.update(shape)
            bones.append(entry)
            bone_abs[name] = absolute
            bone_rotation_abs[name] = absolute_rotation
            progressed = True
        if not progressed:
            for bone in next_pending:
                name = bone.get("id") or _spine_bone_name(bone.get("part", "part"))
                head = bone.get("head") or [0, canvas_height]
                absolute = _spine_point(head, canvas_height, scale)
                shape, absolute_rotation = _spine_bone_shape(bone, absolute, 0.0, canvas_height, scale)
                entry = {"name": name, "parent": "root", "x": round(absolute[0], 3), "y": round(absolute[1], 3)}
                if shape:
                    entry.update(shape)
                bones.append(entry)
                bone_abs[name] = absolute
                bone_rotation_abs[name] = absolute_rotation
            break
        pending = next_pending
    return bones, bone_abs, bone_rotation_abs, export_ids


def _build_spine_atlas(regions):
    lines = []
    for region in regions:
        lines.extend(
            [
                f'{region["page"]}.png',
                f'size: {region["width"]},{region["height"]}',
                "format: RGBA8888",
                "filter: Linear,Linear",
                "repeat: none",
                region["name"],
                "  rotate: false",
                "  xy: 0, 0",
                f'  size: {region["width"]}, {region["height"]}',
                f'  orig: {region["width"]}, {region["height"]}',
                "  offset: 0, 0",
                "  index: -1",
                "",
            ]
        )
    return "\n".join(lines)


def _build_spine_export(parts, rig, export_dir, asset_name, scale, animation_preset, animation_duration, animation_intensity, blink_interval):
    tag2pinfo = parts.get("tag2pinfo")
    frame_size = parts.get("frame_size")
    if not isinstance(tag2pinfo, dict) or frame_size is None:
        raise ValueError("Invalid SEETHROUGH_PARTS data: missing tag2pinfo or frame_size")

    canvas_h, canvas_w = [int(value) for value in frame_size[:2]]
    loaded_rig = _load_rig(rig)
    images_dir = os.path.join(export_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    spine_bones, bone_abs, bone_rotation_abs, export_bone_ids = _build_spine_bones(loaded_rig, canvas_h, scale)
    animations = _build_spine_animations(
        loaded_rig,
        spine_bones,
        animation_preset,
        animation_duration,
        animation_intensity,
        blink_interval,
    )
    slots = []
    attachments = {}
    atlas_regions = []
    exported_parts = []
    skipped_parts = []
    bones_by_id = _rig_bones_by_id(loaded_rig)
    bones_by_part = {bone.get("part"): bone for bone in loaded_rig.get("bones", [])}

    for part in loaded_rig.get("parts", []):
        part_id = part.get("id") or _safe_svg_id(part.get("tag", "part"))
        img, xyxy = _part_image_info(part, tag2pinfo)
        if not isinstance(img, np.ndarray) or img.ndim != 3:
            skipped_parts.append(part_id)
            continue

        height, width = int(img.shape[0]), int(img.shape[1])
        if width <= 0 or height <= 0:
            skipped_parts.append(part_id)
            continue

        image_path = os.path.join(images_dir, f"{part_id}.png")
        if not _write_part_png(img, image_path):
            skipped_parts.append(part_id)
            continue

        bone_name = _attachment_bone_for_part(part, loaded_rig, bones_by_part, bones_by_id, export_bone_ids)
        x1, y1, x2, y2 = xyxy
        center = [(x1 + x2) / 2.0, (y1 + y2) / 2.0]
        center_spine = _spine_point(center, canvas_h, scale)
        bone_position = bone_abs.get(bone_name, [0.0, 0.0])
        bone_rotation = bone_rotation_abs.get(bone_name, 0.0)
        local_center = _to_local_point(center_spine, bone_position, bone_rotation)
        attachment_name = part_id
        attachment_path = part_id

        slots.append({"name": part_id, "bone": bone_name, "attachment": attachment_name})
        attachments[part_id] = {
            attachment_name: {
                "type": "region",
                "path": attachment_path,
                "x": round(local_center[0], 3),
                "y": round(local_center[1], 3),
                "rotation": round(-bone_rotation, 3),
                "width": round(width * float(scale), 3),
                "height": round(height * float(scale), 3),
            }
        }
        atlas_regions.append({"page": f"images/{part_id}", "name": attachment_path, "width": width, "height": height})
        exported_parts.append(part_id)

    spine_json = {
        "skeleton": {
            "hash": "",
            "spine": "4.1",
            "x": 0,
            "y": 0,
            "width": round(canvas_w * float(scale), 3),
            "height": round(canvas_h * float(scale), 3),
            "images": "./images/",
        },
        "bones": spine_bones,
        "slots": slots,
        "skins": [{"name": "default", "attachments": attachments}],
        "animations": animations,
    }

    spine_json_text = json.dumps(spine_json, indent=2, ensure_ascii=False)
    atlas_text = _build_spine_atlas(atlas_regions)
    json_path = os.path.join(export_dir, f"{asset_name}.json")
    atlas_path = os.path.join(export_dir, f"{asset_name}.atlas")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json_file.write(spine_json_text)
    with open(atlas_path, "w", encoding="utf-8") as atlas_file:
        atlas_file.write(atlas_text)

    return {
        "json": spine_json_text,
        "atlas": atlas_text,
        "export_dir": export_dir,
        "json_path": json_path,
        "atlas_path": atlas_path,
        "images_dir": images_dir,
        "exported_parts": exported_parts,
        "skipped_parts": skipped_parts,
    }


class GameAssets_SeeThroughPartsToSVGPaths:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "parts": ("SEETHROUGH_PARTS",),
                "contour_mode": (SVG_CONTOUR_MODES, {"default": "hierarchy"}),
                "depth_order": (SVG_DEPTH_ORDERS, {"default": "back_to_front"}),
                "alpha_threshold": ("INT", {"default": 10, "min": 0, "max": 255, "step": 1}),
                "simplify_epsilon": ("FLOAT", {"default": 1.5, "min": 0.0, "max": 64.0, "step": 0.1}),
                "min_area": ("INT", {"default": 16, "min": 0, "max": 1048576, "step": 1}),
                "stroke_width": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 64.0, "step": 0.1}),
                "stroke_color": ("STRING", {"default": "#111111"}),
                "fill_paths": ("BOOLEAN", {"default": False}),
                "fill_color": ("STRING", {"default": "none"}),
                "save_svg": ("BOOLEAN", {"default": True}),
                "filename_prefix": ("STRING", {"default": "seethrough_trace"}),
            }
        }

    RETURN_TYPES = ("SVG", "STRING", "STRING")
    RETURN_NAMES = ("svg", "svg_text", "svg_path")
    FUNCTION = "trace"
    CATEGORY = "Game Assets/Canvas"

    def trace(
        self,
        parts,
        contour_mode="hierarchy",
        depth_order="back_to_front",
        alpha_threshold=10,
        simplify_epsilon=1.5,
        min_area=16,
        stroke_width=1.0,
        stroke_color="#111111",
        fill_paths=False,
        fill_color="none",
        save_svg=True,
        filename_prefix="seethrough_trace",
    ):
        if not isinstance(parts, dict):
            raise ValueError("Expected SEETHROUGH_PARTS dict from SeeThrough_PostProcess")

        tag2pinfo = parts.get("tag2pinfo")
        frame_size = parts.get("frame_size")
        if not isinstance(tag2pinfo, dict) or frame_size is None:
            raise ValueError("Invalid SEETHROUGH_PARTS data: missing tag2pinfo or frame_size")

        canvas_h, canvas_w = [int(value) for value in frame_size[:2]]
        retrieval_mode = cv2.RETR_EXTERNAL if contour_mode == "external" else cv2.RETR_CCOMP

        path_elements = []
        skipped = 0
        for tag, pinfo in _sort_parts(tag2pinfo, depth_order):
            img = pinfo.get("img")
            if img is None or not isinstance(img, np.ndarray) or img.ndim != 3 or img.shape[-1] < 4:
                skipped += 1
                continue

            alpha = img[..., 3]
            mask = (alpha > int(alpha_threshold)).astype(np.uint8) * 255
            contours, _hierarchy = cv2.findContours(mask, retrieval_mode, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                skipped += 1
                continue

            xyxy = pinfo.get("xyxy", [0, 0, img.shape[1], img.shape[0]])
            offset_x, offset_y = int(xyxy[0]), int(xyxy[1])
            subpaths = []
            for contour in contours:
                area = abs(cv2.contourArea(contour))
                if area < int(min_area):
                    continue
                if simplify_epsilon > 0:
                    contour = cv2.approxPolyDP(contour, float(simplify_epsilon), True)
                path_data = _contour_to_path(contour, offset_x, offset_y, close_path=True)
                if path_data:
                    subpaths.append(path_data)

            if not subpaths:
                skipped += 1
                continue

            depth = pinfo.get("depth_median", "")
            attrs = _svg_attributes(
                id=_safe_svg_id(tag),
                d=" ".join(subpaths),
                fill=fill_color if fill_paths else "none",
                stroke=stroke_color if stroke_width > 0 else "none",
                stroke_width=_format_number(stroke_width),
                fill_rule="evenodd",
                vector_effect="non-scaling-stroke",
                data_tag=tag,
                data_depth=depth,
            )
            path_elements.append(f"  <path {attrs}/>")

        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" viewBox="0 0 {canvas_w} {canvas_h}">',
            "  <title>SeeThrough parts trace</title>",
            f'  <g id="seethrough-parts" data-contour-mode="{escape(contour_mode)}" data-depth-order="{escape(depth_order)}">',
            *path_elements,
            "  </g>",
            "</svg>",
        ]
        svg = "\n".join(svg_lines)
        svg_output = SVGData([BytesIO(svg.encode("utf-8"))])

        svg_path = ""
        if save_svg:
            output_dir = folder_paths.get_output_directory()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = str(uuid.uuid4())[:8]
            safe_prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename_prefix).strip("_") or "seethrough_trace"
            svg_filename = f"{safe_prefix}_{timestamp}_{suffix}.svg"
            svg_path = os.path.join(output_dir, svg_filename)
            with open(svg_path, "w", encoding="utf-8") as svg_file:
                svg_file.write(svg)

        print(
            f"[GameAssetsMaker] Traced {len(path_elements)} SVG part paths "
            f"({skipped} skipped) from SeeThrough parts",
            flush=True,
        )
        return (svg_output, svg, svg_path)


class GameAssets_SeeThroughPartsRigProbe:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "parts": ("SEETHROUGH_PARTS",),
                "alpha_threshold": ("INT", {"default": 10, "min": 0, "max": 255, "step": 1}),
                "depth_order": (SVG_DEPTH_ORDERS, {"default": "back_to_front"}),
            }
        }

    RETURN_TYPES = ("GAME_ASSET_RIG", "STRING", "STRING")
    RETURN_NAMES = ("rig", "rig_json", "report")
    FUNCTION = "probe"
    CATEGORY = "Game Assets/Rigging"

    def probe(self, parts, alpha_threshold=10, depth_order="back_to_front"):
        if not isinstance(parts, dict):
            raise ValueError("Expected SEETHROUGH_PARTS dict from SeeThrough_PostProcess")

        tag2pinfo = parts.get("tag2pinfo")
        frame_size = parts.get("frame_size")
        if not isinstance(tag2pinfo, dict) or frame_size is None:
            raise ValueError("Invalid SEETHROUGH_PARTS data: missing tag2pinfo or frame_size")

        canvas_h, canvas_w = [int(value) for value in frame_size[:2]]
        rig_parts = []
        for tag, pinfo in _sort_parts(tag2pinfo, depth_order):
            role = _detect_role(tag)
            side = _detect_side(tag)
            bounds, centroid = _part_bounds(pinfo, alpha_threshold)
            pivot = _pivot_for_role(role, bounds, centroid)
            part_id = _safe_svg_id(tag)
            rig_parts.append(
                {
                    "id": part_id,
                    "tag": str(tag),
                    "role": role,
                    "side": side,
                    "bounds": bounds,
                    "pivot": _round_point(pivot),
                    "centroid": _round_point(centroid),
                    "depth": pinfo.get("depth_median", None),
                }
            )

        bones = []
        for part in rig_parts:
            parent_part_id = _suggest_parent(part, rig_parts)
            bones.append(
                {
                    "id": f'{part["id"]}_bone',
                    "part": part["id"],
                    "parent": f"{parent_part_id}_bone" if parent_part_id else None,
                    "role": part["role"],
                    "side": part["side"],
                    "head": part["pivot"],
                    "tail": _round_point(_bone_tail_for_role(part["role"], part["bounds"], part["pivot"])),
                }
            )

        known_parts = [part for part in rig_parts if part["role"] != "unknown"]
        rig = {
            "version": 1,
            "source": "SEETHROUGH_PARTS",
            "canvas": {"width": canvas_w, "height": canvas_h},
            "stats": {
                "parts": len(rig_parts),
                "recognized": len(known_parts),
                "unknown": len(rig_parts) - len(known_parts),
            },
            "parts": rig_parts,
            "bones": bones,
        }
        rig_json = json.dumps(rig, indent=2, ensure_ascii=False)

        lines = [
            "SeeThrough rig probe",
            f"canvas: {canvas_w}x{canvas_h}",
            f"parts: {len(rig_parts)} | recognized: {len(known_parts)} | unknown: {len(rig_parts) - len(known_parts)}",
            "",
        ]
        for part, bone in zip(rig_parts, bones):
            parent = bone["parent"] or "root"
            lines.append(
                f'- {part["tag"]}: role={part["role"]}, side={part["side"]}, '
                f'pivot={part["pivot"]}, parent={parent}'
            )
        report = "\n".join(lines)
        print(f"[GameAssetsMaker] Rig probe recognized {len(known_parts)}/{len(rig_parts)} tagged parts", flush=True)
        return (rig, rig_json, report)


class GameAssets_ApplyRigOverrides:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "rig": ("GAME_ASSET_RIG",),
                "overrides_json": (
                    "STRING",
                    {
                        "default": '{\n  "parts": {},\n  "bones": {}\n}',
                        "multiline": True,
                    },
                ),
            }
        }

    RETURN_TYPES = ("GAME_ASSET_RIG", "STRING", "STRING")
    RETURN_NAMES = ("rig", "rig_json", "report")
    FUNCTION = "apply"
    CATEGORY = "Game Assets/Rigging"

    def apply(self, rig, overrides_json):
        adjusted_rig = _load_rig(rig)
        overrides = _load_overrides(overrides_json)
        changed_parts, changed_bones = _apply_rig_overrides(adjusted_rig, overrides)
        rig_json = json.dumps(adjusted_rig, indent=2, ensure_ascii=False)

        report = _rig_report(adjusted_rig, title="Adjusted rig")
        report += "\n\nOverrides applied"
        report += f"\nparts: {', '.join(changed_parts) if changed_parts else 'none'}"
        report += f"\nbones: {', '.join(changed_bones) if changed_bones else 'none'}"
        print(
            f"[GameAssetsMaker] Applied rig overrides to {len(changed_parts)} parts and {len(changed_bones)} bones",
            flush=True,
        )
        return (adjusted_rig, rig_json, report)


class GameAssets_SeeThroughPartsPoseRig:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "parts": ("SEETHROUGH_PARTS",),
                "pose_kps": ("POSE_KEYPOINT",),
                "person_index": ("INT", {"default": 0, "min": 0, "max": 64, "step": 1}),
                "pose_confidence_threshold": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01}),
                "alpha_threshold": ("INT", {"default": 10, "min": 0, "max": 255, "step": 1}),
                "depth_order": (SVG_DEPTH_ORDERS, {"default": "back_to_front"}),
            }
        }

    RETURN_TYPES = ("GAME_ASSET_RIG", "STRING", "STRING")
    RETURN_NAMES = ("rig", "rig_json", "report")
    FUNCTION = "rig_from_pose"
    CATEGORY = "Game Assets/Rigging"

    def rig_from_pose(
        self,
        parts,
        pose_kps,
        person_index=0,
        pose_confidence_threshold=0.05,
        alpha_threshold=10,
        depth_order="back_to_front",
    ):
        rig, _rig_json, _report = GameAssets_SeeThroughPartsRigProbe().probe(parts, alpha_threshold, depth_order)
        person, pose_width, pose_height = _pose_person(pose_kps, int(person_index))
        if person is None:
            rig["pose"] = {"error": f"No person found at index {person_index}"}
            rig_json = json.dumps(rig, indent=2, ensure_ascii=False)
            report = _rig_report(rig, title="Pose rig fallback")
            report += f"\n\nNo DWPose/OpenPose person found at index {person_index}; using automatic tag/bounds pivots."
            return (rig, rig_json, report)

        canvas = rig.get("canvas", {})
        canvas_width = int(canvas.get("width", pose_width or 0))
        canvas_height = int(canvas.get("height", pose_height or 0))
        pose_points = _pose_points_from_person(person, canvas_width, canvas_height, pose_confidence_threshold)
        part_sources, bone_sources = _apply_pose_points_to_rig(rig, pose_points)
        rig["pose"]["person_index"] = int(person_index)
        rig["pose"]["confidence_threshold"] = float(pose_confidence_threshold)
        rig_json = json.dumps(rig, indent=2, ensure_ascii=False)

        report = _rig_report(rig, title="DWPose assisted rig")
        report += "\n\nPose keypoints"
        report += f"\npoints detected: {len(pose_points)}"
        report += f"\npart pivots from pose: {len(part_sources)}"
        report += f"\nbone tails from pose: {len(bone_sources)}"
        if part_sources:
            report += "\n"
            for part_id, source in part_sources.items():
                report += f"\n- {part_id}: pose:{source}"
        print(
            f"[GameAssetsMaker] Built pose rig with {len(part_sources)} pose pivots and {len(bone_sources)} pose bone tails",
            flush=True,
        )
        return (rig, rig_json, report)


class GameAssets_RigToSpineExport:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "parts": ("SEETHROUGH_PARTS",),
                "rig": ("GAME_ASSET_RIG",),
                "filename_prefix": ("STRING", {"default": "game_asset_spine"}),
                "scale": ("FLOAT", {"default": 1.0, "min": 0.001, "max": 100.0, "step": 0.001}),
                "animation_preset": (SPINE_ANIMATION_PRESETS, {"default": "all"}),
                "animation_duration": ("FLOAT", {"default": 2.0, "min": 0.25, "max": 30.0, "step": 0.05}),
                "animation_intensity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 5.0, "step": 0.05}),
                "blink_interval": ("FLOAT", {"default": 3.0, "min": 0.25, "max": 30.0, "step": 0.05}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("spine_json", "json_path", "atlas_path", "images_dir", "report")
    FUNCTION = "export"
    CATEGORY = "Game Assets/Rigging"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return SPINE_EXPORT_VERSION

    def export(
        self,
        parts,
        rig,
        filename_prefix="game_asset_spine",
        scale=1.0,
        animation_preset="all",
        animation_duration=2.0,
        animation_intensity=1.0,
        blink_interval=3.0,
    ):
        if not isinstance(parts, dict):
            raise ValueError("Expected SEETHROUGH_PARTS dict from SeeThrough_PostProcess")
        safe_prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename_prefix).strip("_") or "game_asset_spine"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = str(uuid.uuid4())[:8]
        asset_name = f"{safe_prefix}_{timestamp}_{suffix}"
        export_dir = os.path.join(folder_paths.get_output_directory(), asset_name)
        os.makedirs(export_dir, exist_ok=True)

        result = _build_spine_export(
            parts,
            rig,
            export_dir,
            asset_name,
            float(scale),
            animation_preset,
            float(animation_duration),
            float(animation_intensity),
            float(blink_interval),
        )
        animation_names = sorted(json.loads(result["json"]).get("animations", {}).keys())
        report = "\n".join(
            [
                "Spine export",
                f"json: {result['json_path']}",
                f"atlas: {result['atlas_path']}",
                f"images: {result['images_dir']}",
                f"exported parts: {len(result['exported_parts'])}",
                f"skipped parts: {len(result['skipped_parts'])}",
                f"skipped ids: {', '.join(result['skipped_parts']) if result['skipped_parts'] else 'none'}",
                f"animations: {', '.join(animation_names) if animation_names else 'none'}",
            ]
        )
        print(
            f"[GameAssetsMaker] Exported Spine rig with {len(result['exported_parts'])} region attachments",
            flush=True,
        )
        return {
            "ui": {
                "game_assets_spine": [
                    {
                        "asset_name": asset_name,
                        "export_dir": result["export_dir"],
                        "json_path": result["json_path"],
                        "atlas_path": result["atlas_path"],
                        "images_dir": result["images_dir"],
                    }
                ]
            },
            "result": (result["json"], result["json_path"], result["atlas_path"], result["images_dir"], report),
        }


class GameAssets_RigToSVGPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "rig": ("GAME_ASSET_RIG",),
                "show_bounds": ("BOOLEAN", {"default": True}),
                "show_labels": ("BOOLEAN", {"default": True}),
                "stroke_width": ("FLOAT", {"default": 4.0, "min": 0.25, "max": 64.0, "step": 0.25}),
                "bone_color": ("STRING", {"default": "#2f80ed"}),
                "pivot_color": ("STRING", {"default": "#ff3366"}),
                "label_color": ("STRING", {"default": "#111111"}),
            }
        }

    RETURN_TYPES = ("SVG", "STRING")
    RETURN_NAMES = ("svg", "svg_text")
    FUNCTION = "render"
    CATEGORY = "Game Assets/Rigging"

    def render(
        self,
        rig,
        show_bounds=True,
        show_labels=True,
        stroke_width=4.0,
        bone_color="#2f80ed",
        pivot_color="#ff3366",
        label_color="#111111",
    ):
        loaded_rig = _load_rig(rig)
        svg = _rig_to_svg(loaded_rig, show_bounds, show_labels, bone_color, pivot_color, label_color, stroke_width)
        svg_output = SVGData([BytesIO(svg.encode("utf-8"))])
        print(f"[GameAssetsMaker] Rendered rig SVG preview with {len(loaded_rig.get('bones', []))} bones", flush=True)
        return (svg_output, svg)


class GameAssets_AsepriteVisualNovelAtlas:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "sheet_width": ("INT", {"default": 2048, "min": 1, "max": 65536, "step": 1}),
                "sheet_height": ("INT", {"default": 2048, "min": 1, "max": 65536, "step": 1}),
                "spritesheet_type": (ASEPRITE_SPRITESHEET_TYPES, {"default": "Half Body"}),
                "sprite_count": ("INT", {"default": 4, "min": 1, "max": 128, "step": 1}),
                "layout_direction": (ASEPRITE_LAYOUT_DIRECTIONS, {"default": "Horizontal"}),
                "columns": ("INT", {"default": 0, "min": 0, "max": 128, "step": 1}),
                "sprite_names": (
                    "STRING",
                    {
                        "default": '["neutral", "happy", "sad", "angry"]',
                    },
                ),
                "image_filename": ("STRING", {"default": "visual_novel_character.png"}),
                "frame_duration": ("INT", {"default": 100, "min": 1, "max": 60000, "step": 1}),
                "save_json": ("BOOLEAN", {"default": True}),
                "filename_prefix": ("STRING", {"default": "vn_character_atlas"}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("aseprite_json", "json_path", "report")
    FUNCTION = "generate"
    CATEGORY = "Game Assets/Aseprite"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return ASEPRITE_EXPORT_VERSION

    def generate(
        self,
        sheet_width=2048,
        sheet_height=2048,
        spritesheet_type="Half Body",
        sprite_count=4,
        layout_direction="Horizontal",
        columns=0,
        sprite_names='["neutral", "happy", "sad", "angry"]',
        image_filename="visual_novel_character.png",
        frame_duration=100,
        save_json=True,
        filename_prefix="vn_character_atlas",
    ):
        atlas = _build_aseprite_atlas(
            sheet_width,
            sheet_height,
            spritesheet_type,
            sprite_count,
            layout_direction,
            sprite_names,
            columns,
            image_filename,
            frame_duration,
        )
        aseprite_json = json.dumps(atlas, indent=2, ensure_ascii=False)

        json_path = ""
        if save_json:
            output_dir = folder_paths.get_output_directory()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = str(uuid.uuid4())[:8]
            safe_prefix = _safe_filename_stem(filename_prefix, "vn_character_atlas")
            json_filename = f"{safe_prefix}_{timestamp}_{suffix}.json"
            json_path = os.path.join(output_dir, json_filename)
            with open(json_path, "w", encoding="utf-8") as json_file:
                json_file.write(aseprite_json)

        layout = atlas["meta"]["layout"]
        report = "\n".join(
            [
                "Aseprite visual novel atlas",
                f"type: {spritesheet_type}",
                f"spritesheet: {int(sheet_width)}x{int(sheet_height)}",
                f"sprites: {layout['spriteCount']}",
                f"direction: {layout['direction']}",
                f"layout: {layout['columns']} columns x {layout['rows']} rows",
                f"frame: {layout['frameWidth']}x{layout['frameHeight']}",
                f"image: {image_filename}",
                f"json: {json_path or 'not saved'}",
            ]
        )
        print(
            f"[GameAssetsMaker] Generated Aseprite atlas with {layout['spriteCount']} frames "
            f"({layout['columns']}x{layout['rows']})",
            flush=True,
        )
        return (aseprite_json, json_path, report)


class GameAssets_AsepriteAtlasSpritePreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "spritesheet": ("IMAGE",),
                "aseprite_json": ("STRING", {"default": "", "multiline": True}),
                "image_index": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 1}),
                "background_value": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("sprites", "sprite_names", "report")
    FUNCTION = "preview"
    CATEGORY = "Game Assets/Aseprite"

    def preview(self, spritesheet, aseprite_json, image_index=0, background_value=0.0):
        atlas = _load_aseprite_atlas(aseprite_json)
        frame_items = _aseprite_frame_items(atlas)
        sprites, clipped = _extract_aseprite_sprites(spritesheet, frame_items, image_index, background_value)

        names = [item["name"] for item in frame_items]
        max_w = max(item["w"] for item in frame_items)
        max_h = max(item["h"] for item in frame_items)
        lines = [
            "Aseprite sprite preview",
            f"sprites: {len(frame_items)}",
            f"output batch: {len(frame_items)} images",
            f"canvas per sprite: {max_w}x{max_h}",
            f"source image index: {max(0, min(int(image_index), int(spritesheet.shape[0]) - 1))}",
            f"clipped frames: {', '.join(clipped) if clipped else 'none'}",
            "",
            "frames:",
        ]
        for item in frame_items:
            lines.append(f"- {item['name']}: x={item['x']} y={item['y']} w={item['w']} h={item['h']}")

        report = "\n".join(lines)
        print(f"[GameAssetsMaker] Extracted {len(frame_items)} Aseprite sprites for preview", flush=True)
        return (sprites, "\n".join(names), report)


class GameAssets_AsepriteAnimationTags:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "frame_count": ("INT", {"default": 4, "min": 1, "max": 10000, "step": 1}),
                "animation_count": ("INT", {"default": 1, "min": 1, "max": 128, "step": 1}),
                "animation_tags": (
                    "STRING",
                    {
                        "default": '[{"name":"idle","from":0,"to":3,"direction":"forward","color":"#000000ff"}]',
                    },
                ),
                "save_json": ("BOOLEAN", {"default": True}),
                "filename_prefix": ("STRING", {"default": "aseprite_animation_tags"}),
            },
            "optional": {
                "aseprite_json": ("STRING", {"default": "", "multiline": True}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("animation_json", "merged_aseprite_json", "json_path", "report")
    FUNCTION = "generate"
    CATEGORY = "Game Assets/Aseprite"
    OUTPUT_NODE = True

    def generate(
        self,
        frame_count=4,
        animation_count=1,
        animation_tags='[{"name":"idle","from":0,"to":3,"direction":"forward","color":"#000000ff"}]',
        save_json=True,
        filename_prefix="aseprite_animation_tags",
        aseprite_json="",
    ):
        merged, tags = _build_aseprite_animation_json(aseprite_json, frame_count, animation_count, animation_tags)
        animation_payload = {"meta": {"frameTags": tags}}
        animation_json = json.dumps(animation_payload, indent=2, ensure_ascii=False)
        merged_json = json.dumps(merged, indent=2, ensure_ascii=False)

        json_path = ""
        if save_json:
            output_dir = folder_paths.get_output_directory()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = str(uuid.uuid4())[:8]
            safe_prefix = _safe_filename_stem(filename_prefix, "aseprite_animation_tags")
            json_filename = f"{safe_prefix}_{timestamp}_{suffix}.json"
            json_path = os.path.join(output_dir, json_filename)
            with open(json_path, "w", encoding="utf-8") as json_file:
                json_file.write(merged_json)

        lines = [
            "Aseprite animation tags",
            f"frame count: {max(1, int(frame_count))}",
            f"animations: {len(tags)}",
            f"merged atlas: {'yes' if str(aseprite_json or '').strip() else 'no'}",
            f"json: {json_path or 'not saved'}",
            "",
            "tags:",
        ]
        for tag in tags:
            lines.append(
                f"- {tag['name']}: frames {tag['from']}..{tag['to']} "
                f"direction={tag['direction']} color={tag['color']}"
            )
        report = "\n".join(lines)
        print(f"[GameAssetsMaker] Generated {len(tags)} Aseprite animation frame tags", flush=True)
        return (animation_json, merged_json, json_path, report)


class GameAssets_AsepriteAnimationAtlas:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "sheet_width": ("INT", {"default": 1024, "min": 1, "max": 65536, "step": 1}),
                "sheet_height": ("INT", {"default": 1024, "min": 1, "max": 65536, "step": 1}),
                "frame_count": ("INT", {"default": 4, "min": 1, "max": 10000, "step": 1}),
                "layout_direction": (ASEPRITE_LAYOUT_DIRECTIONS, {"default": "Horizontal"}),
                "columns": ("INT", {"default": 0, "min": 0, "max": 128, "step": 1}),
                "animation_count": ("INT", {"default": 1, "min": 1, "max": 128, "step": 1}),
                "animation_tags": (
                    "STRING",
                    {
                        "default": '[{"name":"idle","from":0,"to":3,"direction":"forward","color":"#000000ff"}]',
                    },
                ),
                "image_filename": ("STRING", {"default": "animation_spritesheet.png"}),
                "frame_duration": ("INT", {"default": 100, "min": 1, "max": 60000, "step": 1}),
                "frame_name_prefix": ("STRING", {"default": "frame"}),
                "save_json": ("BOOLEAN", {"default": True}),
                "filename_prefix": ("STRING", {"default": "aseprite_animation_atlas"}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("aseprite_json", "json_path", "report")
    FUNCTION = "generate"
    CATEGORY = "Game Assets/Aseprite"
    OUTPUT_NODE = True

    def generate(
        self,
        sheet_width=1024,
        sheet_height=1024,
        frame_count=4,
        layout_direction="Horizontal",
        columns=0,
        animation_count=1,
        animation_tags='[{"name":"idle","from":0,"to":3,"direction":"forward","color":"#000000ff"}]',
        image_filename="animation_spritesheet.png",
        frame_duration=100,
        frame_name_prefix="frame",
        save_json=True,
        filename_prefix="aseprite_animation_atlas",
    ):
        atlas, tags, frame_names = _build_aseprite_animation_atlas(
            sheet_width,
            sheet_height,
            frame_count,
            layout_direction,
            columns,
            animation_count,
            animation_tags,
            image_filename,
            frame_duration,
            frame_name_prefix,
        )
        aseprite_json = json.dumps(atlas, indent=2, ensure_ascii=False)

        json_path = ""
        if save_json:
            output_dir = folder_paths.get_output_directory()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = str(uuid.uuid4())[:8]
            safe_prefix = _safe_filename_stem(filename_prefix, "aseprite_animation_atlas")
            json_filename = f"{safe_prefix}_{timestamp}_{suffix}.json"
            json_path = os.path.join(output_dir, json_filename)
            with open(json_path, "w", encoding="utf-8") as json_file:
                json_file.write(aseprite_json)

        layout = atlas["meta"]["layout"]
        lines = [
            "Aseprite animation atlas",
            f"spritesheet: {int(sheet_width)}x{int(sheet_height)}",
            f"frames: {layout['spriteCount']}",
            f"direction: {layout['direction']}",
            f"layout: {layout['columns']} columns x {layout['rows']} rows",
            f"frame: {layout['frameWidth']}x{layout['frameHeight']}",
            f"image: {image_filename}",
            f"json: {json_path or 'not saved'}",
            "",
            "animations:",
        ]
        for tag in tags:
            lines.append(
                f"- {tag['name']}: frames {tag['from']}..{tag['to']} "
                f"direction={tag['direction']} color={tag['color']}"
            )
        lines.extend(["", "frame names:", *[f"- {name}.png" for name in frame_names]])
        report = "\n".join(lines)
        print(
            f"[GameAssetsMaker] Generated animation atlas with {len(frame_names)} frames and {len(tags)} tags",
            flush=True,
        )
        return (aseprite_json, json_path, report)


class GameAssets_AsepriteAnimationPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "spritesheet": ("IMAGE",),
                "aseprite_json": ("STRING", {"default": "", "multiline": True}),
                "animation_name": ("STRING", {"default": "idle"}),
                "image_index": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 1}),
                "loop_count": ("INT", {"default": 1, "min": 1, "max": 64, "step": 1}),
                "include_pingpong_endpoint": ("BOOLEAN", {"default": False}),
                "background_value": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "INT", "STRING")
    RETURN_NAMES = ("animation_frames", "frame_names", "frame_delay_ms", "report")
    FUNCTION = "preview"
    CATEGORY = "Game Assets/Aseprite"

    def preview(
        self,
        spritesheet,
        aseprite_json,
        animation_name="idle",
        image_index=0,
        loop_count=1,
        include_pingpong_endpoint=False,
        background_value=0.0,
    ):
        atlas = _load_aseprite_atlas(aseprite_json)
        frame_items = _aseprite_frame_items(atlas)
        tags = _aseprite_frame_tags(atlas)
        if not tags:
            raise ValueError("Aseprite JSON does not contain meta.frameTags")

        selected = None
        requested = str(animation_name or "").strip()
        for tag in tags:
            if tag["name"] == requested:
                selected = tag
                break
        if selected is None:
            selected = tags[0]

        indexes = _animation_frame_indexes(
            selected,
            len(frame_items),
            loop_count=loop_count,
            include_pingpong_endpoint=include_pingpong_endpoint,
        )
        if not indexes:
            raise ValueError(f"Animation {selected['name']} does not resolve to any frames")

        selected_items = [frame_items[index] for index in indexes]
        animation_frames, clipped = _extract_aseprite_sprites(
            spritesheet,
            selected_items,
            image_index=image_index,
            background_value=background_value,
        )
        names = [item["name"] for item in selected_items]

        durations = []
        raw_frames = atlas.get("frames", {})
        for item in selected_items:
            frame_data = raw_frames.get(item["name"], {}) if isinstance(raw_frames, dict) else {}
            try:
                durations.append(int(frame_data.get("duration", 100)))
            except (TypeError, ValueError):
                durations.append(100)
        frame_delay_ms = int(round(sum(durations) / len(durations))) if durations else 100

        report = "\n".join(
            [
                "Aseprite animation preview",
                f"requested animation: {requested or '(first tag)'}",
                f"selected animation: {selected['name']}",
                f"direction: {selected['direction']}",
                f"source range: {selected['from']}..{selected['to']}",
                f"output frames: {len(indexes)}",
                f"frame indexes: {', '.join(str(index) for index in indexes)}",
                f"frame delay: {frame_delay_ms} ms",
                f"clipped frames: {', '.join(clipped) if clipped else 'none'}",
                f"available animations: {', '.join(tag['name'] for tag in tags)}",
            ]
        )
        print(
            f"[GameAssetsMaker] Built animation preview '{selected['name']}' with {len(indexes)} frames",
            flush=True,
        )
        return (animation_frames, "\n".join(names), frame_delay_ms, report)


NODE_CLASS_MAPPINGS = {
    "GameAssets_SeeThroughPartsToSVGPaths": GameAssets_SeeThroughPartsToSVGPaths,
    "GameAssets_SeeThroughPartsRigProbe": GameAssets_SeeThroughPartsRigProbe,
    "GameAssets_SeeThroughPartsPoseRig": GameAssets_SeeThroughPartsPoseRig,
    "GameAssets_ApplyRigOverrides": GameAssets_ApplyRigOverrides,
    "GameAssets_RigToSpineExport": GameAssets_RigToSpineExport,
    "GameAssets_RigToSVGPreview": GameAssets_RigToSVGPreview,
    "GameAssets_AsepriteVisualNovelAtlas": GameAssets_AsepriteVisualNovelAtlas,
    "GameAssets_AsepriteAtlasSpritePreview": GameAssets_AsepriteAtlasSpritePreview,
    "GameAssets_AsepriteAnimationTags": GameAssets_AsepriteAnimationTags,
    "GameAssets_AsepriteAnimationAtlas": GameAssets_AsepriteAnimationAtlas,
    "GameAssets_AsepriteAnimationPreview": GameAssets_AsepriteAnimationPreview,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GameAssets_SeeThroughPartsToSVGPaths": "SeeThrough Parts To SVG Paths",
    "GameAssets_SeeThroughPartsRigProbe": "SeeThrough Parts Rig Probe",
    "GameAssets_SeeThroughPartsPoseRig": "SeeThrough Parts Pose Rig",
    "GameAssets_ApplyRigOverrides": "Apply Rig Overrides",
    "GameAssets_RigToSpineExport": "Rig To Spine Export",
    "GameAssets_RigToSVGPreview": "Rig To SVG Preview",
    "GameAssets_AsepriteVisualNovelAtlas": "Aseprite Visual Novel Atlas",
    "GameAssets_AsepriteAtlasSpritePreview": "Aseprite Atlas Sprite Preview",
    "GameAssets_AsepriteAnimationTags": "Aseprite Animation Tags",
    "GameAssets_AsepriteAnimationAtlas": "Aseprite Animation Atlas",
    "GameAssets_AsepriteAnimationPreview": "Aseprite Animation Preview",
}
