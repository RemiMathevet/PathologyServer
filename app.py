#!/usr/bin/env python3
"""
FoetoPath MRXS Slide Viewer
Flask server for browsing and viewing Mirax (.mrxs) whole-slide images.
Uses OpenSlide + OpenSeadragon for deep-zoom tile-based viewing.

Usage:
    python app.py                        # Start on port 5000, pick folder in GUI
    python app.py --port 8080            # Custom port
    python app.py --root /path/to/slides # Pre-set root folder
"""

import argparse
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from openslide import OpenSlide
from openslide.deepzoom import DeepZoomGenerator
from PIL import Image

app = Flask(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
TILE_SIZE = 254
TILE_OVERLAP = 1
TILE_FORMAT = "jpeg"
TILE_QUALITY = 80
THUMBNAIL_SIZE = (300, 300)
SLIDE_EXTENSIONS = {".mrxs", ".svs", ".ndpi", ".tiff", ".tif", ".scn", ".bif", ".vms", ".vmu"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tga"}


# ── Slide Cache ────────────────────────────────────────────────────────────
@lru_cache(maxsize=10)
def get_slide(slide_path: str) -> OpenSlide:
    """Open and cache an OpenSlide object."""
    return OpenSlide(slide_path)


@lru_cache(maxsize=10)
def get_dz(slide_path: str) -> DeepZoomGenerator:
    """Create and cache a DeepZoomGenerator."""
    slide = get_slide(slide_path)
    return DeepZoomGenerator(slide, tile_size=TILE_SIZE, overlap=TILE_OVERLAP, limit_bounds=True)


def find_slides(folder: str) -> list[dict]:
    """Find all supported slide files in a folder."""
    slides = []
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return slides
    for f in sorted(folder_path.iterdir()):
        if f.suffix.lower() in SLIDE_EXTENSIONS and f.is_file():
            slides.append({
                "name": f.stem,
                "filename": f.name,
                "path": str(f),
                "extension": f.suffix.lower(),
            })
    return slides


def find_photos(folder: str) -> list[dict]:
    """Find all photo/image files in a folder."""
    photos = []
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return photos
    for f in sorted(folder_path.iterdir()):
        if f.suffix.lower() in PHOTO_EXTENSIONS and f.is_file():
            # Skip very small files (thumbnails, icons)
            try:
                size = f.stat().st_size
                if size < 1024:  # < 1KB
                    continue
            except OSError:
                continue
            photos.append({
                "name": f.stem,
                "filename": f.name,
                "path": str(f),
                "extension": f.suffix.lower(),
                "size_kb": round(size / 1024, 1),
            })
    return photos


def find_cases(root: str) -> list[dict]:
    """Find all subfolders (cases) that contain slides or photos."""
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    cases = []
    # Check root itself
    root_slides = find_slides(root)
    root_photos = find_photos(root)
    if root_slides or root_photos:
        cases.append({
            "name": root_path.name,
            "path": str(root_path),
            "slide_count": len(root_slides),
            "photo_count": len(root_photos),
            "is_root": True,
        })

    # Check subfolders
    for d in sorted(root_path.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            slides = find_slides(str(d))
            photos = find_photos(str(d))
            if slides or photos:
                cases.append({
                    "name": d.name,
                    "path": str(d),
                    "slide_count": len(slides),
                    "photo_count": len(photos),
                    "is_root": False,
                })
    return cases


# ── Routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    """Main page."""
    return render_template("index.html", default_root=app.config.get("DEFAULT_ROOT", ""))


@app.route("/api/browse", methods=["POST"])
def browse():
    """List cases (subfolders with slides) in a root directory."""
    data = request.get_json()
    root = data.get("root", "")
    if not root or not os.path.isdir(root):
        return jsonify({"error": "Dossier invalide", "cases": []}), 400
    cases = find_cases(root)
    return jsonify({"cases": cases, "root": root})


@app.route("/api/slides", methods=["POST"])
def slides():
    """List slides and photos in a case folder."""
    data = request.get_json()
    folder = data.get("folder", "")
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "Dossier invalide", "slides": [], "photos": []}), 400
    slide_list = find_slides(folder)
    photo_list = find_photos(folder)
    return jsonify({"slides": slide_list, "photos": photo_list, "folder": folder})


@app.route("/api/slide/info", methods=["POST"])
def slide_info():
    """Get slide metadata."""
    data = request.get_json()
    path = data.get("path", "")
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        slide = get_slide(path)
        dz = get_dz(path)
        props = dict(slide.properties)
        return jsonify({
            "dimensions": slide.dimensions,
            "level_count": slide.level_count,
            "level_dimensions": list(slide.level_dimensions),
            "dz_level_count": dz.level_count,
            "tile_size": TILE_SIZE,
            "overlap": TILE_OVERLAP,
            "properties": {k: v for k, v in props.items() if len(v) < 500},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/slide/dzi", methods=["POST"])
def slide_dzi():
    """Generate DZI XML descriptor for OpenSeadragon."""
    data = request.get_json()
    path = data.get("path", "")
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        dz = get_dz(path)
        resp = dz.get_dzi(TILE_FORMAT)
        return Response(resp, mimetype="application/xml")
    except Exception as e:
        return Response(f"<error>{e}</error>", status=500, mimetype="application/xml")


@app.route("/api/slide/tile/<int:level>/<int:col>_<int:row>.<fmt>")
def slide_tile(level: int, col: int, row: int, fmt: str):
    """Serve a single tile. Slide path passed as query param."""
    path = request.args.get("path", "")
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        dz = get_dz(path)
        tile = dz.get_tile(level, (col, row))
        buf = io.BytesIO()
        tile.save(buf, format=TILE_FORMAT, quality=TILE_QUALITY)
        buf.seek(0)
        return Response(buf.read(), mimetype=f"image/{TILE_FORMAT}")
    except (ValueError, KeyError):
        abort(404)
    except Exception as e:
        abort(500)


@app.route("/api/slide/thumbnail")
def slide_thumbnail():
    """Generate a thumbnail for the carousel."""
    path = request.args.get("path", "")
    width = int(request.args.get("w", THUMBNAIL_SIZE[0]))
    height = int(request.args.get("h", THUMBNAIL_SIZE[1]))
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        slide = get_slide(path)
        thumb = slide.get_thumbnail((width, height))
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        return Response(buf.read(), mimetype="image/jpeg")
    except Exception as e:
        # Return a placeholder
        img = Image.new("RGB", (width, height), (40, 40, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        return Response(buf.read(), mimetype="image/jpeg")


@app.route("/api/slide/label")
def slide_label():
    """Get the label/macro image if available."""
    path = request.args.get("path", "")
    img_type = request.args.get("type", "label")  # label or macro
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        slide = get_slide(path)
        images = slide.associated_images
        if img_type in images:
            img = images[img_type]
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            buf.seek(0)
            return Response(buf.read(), mimetype="image/jpeg")
        abort(404)
    except Exception:
        abort(404)


@app.route("/api/slide/macro/info")
def slide_macro_info():
    """Get macro image dimensions for annotation coordinate mapping."""
    path = request.args.get("path", "")
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        slide = get_slide(path)
        images = slide.associated_images
        # Try macro first, then label
        for img_type in ("macro", "label"):
            if img_type in images:
                img = images[img_type]
                w, h = img.size
                return jsonify({
                    "type": img_type,
                    "width": w,
                    "height": h,
                    "available_types": list(images.keys()),
                })
        return jsonify({"error": "Pas d'image macro disponible"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Photo Routes ───────────────────────────────────────────────────────────
MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".bmp": "image/bmp", ".webp": "image/webp",
    ".tga": "image/x-tga",
}


@app.route("/api/photo/serve")
def photo_serve():
    """Serve a full-resolution photo."""
    path = request.args.get("path", "")
    if not path or not os.path.isfile(path):
        abort(404)
    ext = Path(path).suffix.lower()
    if ext not in PHOTO_EXTENSIONS:
        abort(403)
    mime = MIME_MAP.get(ext, "image/jpeg")
    try:
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, mimetype=mime)
    except Exception:
        abort(500)


@app.route("/api/photo/thumbnail")
def photo_thumbnail():
    """Generate a thumbnail for a photo."""
    path = request.args.get("path", "")
    width = int(request.args.get("w", 192))
    height = int(request.args.get("h", 192))
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        img = Image.open(path)
        img.thumbnail((width, height), Image.LANCZOS)
        # Convert to RGB if needed (for RGBA/palette images)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        buf_format = "JPEG"
        img.save(buf, format=buf_format, quality=85)
        buf.seek(0)
        return Response(buf.read(), mimetype="image/jpeg")
    except Exception:
        # Placeholder
        img = Image.new("RGB", (width, height), (40, 40, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)
        return Response(buf.read(), mimetype="image/jpeg")


# ── Annotation Routes ──────────────────────────────────────────────────────

def get_annotation_path(root: str, slide_path: str) -> Path:
    """Get the GeoJSON annotation file path for a given slide.
    Stored in {root}/annotations/{slide_stem}.geojson
    """
    root_path = Path(root)
    slide_stem = Path(slide_path).stem
    ann_dir = root_path / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    return ann_dir / f"{slide_stem}.geojson"


def get_macro_annotation_path(root: str, slide_path: str) -> Path:
    """Get the GeoJSON annotation file path for a slide's macro image.
    Stored in {root}/annotations/{slide_stem}_macro.geojson
    """
    root_path = Path(root)
    slide_stem = Path(slide_path).stem
    ann_dir = root_path / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    return ann_dir / f"{slide_stem}_macro.geojson"


def get_slide_calibration(slide_path: str) -> dict:
    """Extract calibration metadata from a slide."""
    try:
        slide = get_slide(slide_path)
        props = slide.properties
        w, h = slide.dimensions

        # MPP (microns per pixel)
        mpp_x = float(props.get("openslide.mpp-x", 0))
        mpp_y = float(props.get("openslide.mpp-y", 0))

        # Bounds
        bounds_x = int(props.get("openslide.bounds-x", 0))
        bounds_y = int(props.get("openslide.bounds-y", 0))
        bounds_w = int(props.get("openslide.bounds-width", w))
        bounds_h = int(props.get("openslide.bounds-height", h))

        # Objective power
        objective = props.get("openslide.objective-power", "")

        return {
            "dimensions_px": [w, h],
            "mpp_x": mpp_x,
            "mpp_y": mpp_y,
            "bounds": {
                "x": bounds_x,
                "y": bounds_y,
                "width": bounds_w,
                "height": bounds_h,
            },
            "objective_power": objective,
            "vendor": props.get("openslide.vendor", ""),
        }
    except Exception:
        return {}


@app.route("/api/annotations/save", methods=["POST"])
def annotations_save():
    """Save annotations as GeoJSON."""
    data = request.get_json()
    root = data.get("root", "")
    slide_path = data.get("slide_path", "")
    features = data.get("features", [])

    if not root or not slide_path:
        return jsonify({"error": "Paramètres manquants"}), 400

    # Get calibration from the slide
    calibration = get_slide_calibration(slide_path)
    mpp_x = calibration.get("mpp_x", 0)
    mpp_y = calibration.get("mpp_y", 0)

    # Build features with dual coordinates (pixels + micrometers)
    geojson_features = []
    for feat in features:
        coords_px = feat.get("coordinates", [])
        props = feat.get("properties", {})

        # Convert pixel coordinates to micrometers
        coords_um = []
        if mpp_x > 0 and mpp_y > 0:
            for ring in coords_px:
                coords_um.append([[pt[0] * mpp_x, pt[1] * mpp_y] for pt in ring])

        geojson_feat = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": coords_px,
            },
            "properties": {
                **props,
                "coordinates_um": coords_um if coords_um else None,
                "unit_px": "pixels (absolute, level 0)",
                "unit_um": f"micrometers (mpp_x={mpp_x}, mpp_y={mpp_y})" if mpp_x > 0 else None,
            },
        }
        geojson_features.append(geojson_feat)

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "slide_name": Path(slide_path).name,
            "slide_path": slide_path,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "generator": "FoetoPath Slide Viewer",
            "annotation_levels": {
                "1": "Macro",
                "2": "Cytoarchitecture",
                "3": "Cellulaire",
            },
            **calibration,
        },
        "features": geojson_features,
    }

    # Write file
    try:
        ann_path = get_annotation_path(root, slide_path)
        with open(ann_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
        return jsonify({
            "ok": True,
            "path": str(ann_path),
            "feature_count": len(geojson_features),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/annotations/load")
def annotations_load():
    """Load annotations GeoJSON for a slide."""
    root = request.args.get("root", "")
    slide_path = request.args.get("slide_path", "")

    if not root or not slide_path:
        return jsonify({"error": "Paramètres manquants"}), 400

    ann_path = get_annotation_path(root, slide_path)
    if not ann_path.is_file():
        return jsonify({"features": [], "exists": False})

    try:
        with open(ann_path, "r", encoding="utf-8") as f:
            geojson = json.load(f)
        return jsonify({
            "exists": True,
            "path": str(ann_path),
            **geojson,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/annotations/macro/save", methods=["POST"])
def annotations_macro_save():
    """Save macro image annotations as GeoJSON (coordinates in pixels)."""
    data = request.get_json()
    root = data.get("root", "")
    slide_path = data.get("slide_path", "")
    features = data.get("features", [])
    macro_dimensions = data.get("macro_dimensions", [0, 0])

    if not root or not slide_path:
        return jsonify({"error": "Paramètres manquants"}), 400

    # Build GeoJSON features (coordinates in macro image pixels)
    geojson_features = []
    for feat in features:
        coords_px = feat.get("coordinates", [])
        props = feat.get("properties", {})
        geojson_feat = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": coords_px,
            },
            "properties": {
                **props,
                "unit": "pixels (macro image)",
            },
        }
        geojson_features.append(geojson_feat)

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "slide_name": Path(slide_path).name,
            "slide_path": slide_path,
            "image_type": "macro",
            "macro_dimensions_px": macro_dimensions,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "generator": "FoetoPath Slide Viewer",
        },
        "features": geojson_features,
    }

    try:
        ann_path = get_macro_annotation_path(root, slide_path)
        with open(ann_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
        return jsonify({
            "ok": True,
            "path": str(ann_path),
            "feature_count": len(geojson_features),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/annotations/macro/load")
def annotations_macro_load():
    """Load macro image annotations GeoJSON."""
    root = request.args.get("root", "")
    slide_path = request.args.get("slide_path", "")

    if not root or not slide_path:
        return jsonify({"error": "Paramètres manquants"}), 400

    ann_path = get_macro_annotation_path(root, slide_path)
    if not ann_path.is_file():
        return jsonify({"features": [], "exists": False})

    try:
        with open(ann_path, "r", encoding="utf-8") as f:
            geojson = json.load(f)
        return jsonify({
            "exists": True,
            "path": str(ann_path),
            **geojson,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/annotations/export", methods=["POST"])
def annotations_export():
    """Export tiles covering each annotation at a chosen pyramid level."""
    data = request.get_json()
    slide_path = data.get("slide_path", "")
    annotations = data.get("annotations", [])  # [{points_px, label, id, level}, ...]
    target_level = int(data.get("target_level", 0))
    tile_size = int(data.get("tile_size", 512))
    fmt = data.get("format", "jpeg")  # "jpeg" or "blosc"

    if not slide_path or not os.path.isfile(slide_path):
        return jsonify({"error": "Lame introuvable"}), 400
    if not annotations:
        return jsonify({"error": "Aucune annotation"}), 400

    try:
        slide = get_slide(slide_path)
    except Exception as e:
        return jsonify({"error": f"Erreur ouverture lame: {e}"}), 500

    # Validate level
    if target_level < 0 or target_level >= slide.level_count:
        return jsonify({"error": f"Niveau {target_level} invalide (0-{slide.level_count - 1})"}), 400

    downsample = slide.level_downsamples[target_level]
    level_dims = slide.level_dimensions[target_level]
    props = slide.properties
    mpp_x = float(props.get("openslide.mpp-x", 0))
    mpp_y = float(props.get("openslide.mpp-y", 0))

    # Bounds offset: DZI with limit_bounds=True means OSD coords start at bounds origin
    # read_region needs absolute level-0 coords, so we add the offset
    bounds_x = int(props.get("openslide.bounds-x", 0))
    bounds_y = int(props.get("openslide.bounds-y", 0))

    # Try importing blosc2
    use_blosc = fmt == "blosc"
    if use_blosc:
        try:
            import blosc2
        except ImportError:
            use_blosc = False
            fmt = "jpeg"

    # Create zip in memory
    tmp = tempfile.SpooledTemporaryFile(max_size=200 * 1024 * 1024)  # 200MB in RAM, then disk
    slide_stem = Path(slide_path).stem

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED if use_blosc else zipfile.ZIP_DEFLATED) as zf:
        manifest_entries = []

        for ann in annotations:
            points = ann.get("points_px", [])
            ann_id = ann.get("id", "unknown")
            ann_label = ann.get("label", "")
            ann_level = ann.get("level", 1)

            if len(points) < 3:
                continue

            # Bounding box in level-0 pixels (annotation coords are in DZI bounded space)
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            bbox_x0 = max(0, int(min(xs)))
            bbox_y0 = max(0, int(min(ys)))
            bbox_x1 = min(slide.dimensions[0], int(max(xs)))
            bbox_y1 = min(slide.dimensions[1], int(max(ys)))

            # Step size in level-0 coordinates (tile_size at target level × downsample)
            step = int(tile_size * downsample)

            # Generate tile grid iterating in level-0 space
            ann_folder = f"{ann_id}_{ann_label}".replace(" ", "_").replace("/", "_")[:60]
            tile_idx = 0

            for read_y_rel in range(bbox_y0, bbox_y1, step):
                for read_x_rel in range(bbox_x0, bbox_x1, step):
                    # Absolute level-0 coords (add bounds offset for read_region)
                    read_x = read_x_rel + bounds_x
                    read_y = read_y_rel + bounds_y

                    # Tile dimensions at target level (handle edges)
                    remaining_x = bbox_x1 - read_x_rel
                    remaining_y = bbox_y1 - read_y_rel
                    tw = min(tile_size, int(remaining_x / downsample))
                    th = min(tile_size, int(remaining_y / downsample))
                    if tw <= 0 or th <= 0:
                        continue

                    try:
                        region = slide.read_region((read_x, read_y), target_level, (tw, th))
                        region = region.convert("RGB")
                    except Exception:
                        continue

                    tile_name = f"tile_{tile_idx:04d}_x{read_x_rel}_y{read_y_rel}"
                    tile_meta = {
                        "tile_name": tile_name,
                        "annotation_id": ann_id,
                        "label": ann_label,
                        "annotation_level": ann_level,
                        "pyramid_level": target_level,
                        "downsample": downsample,
                        "tile_size_px": [tw, th],
                        "position_level0_px": [read_x, read_y],
                        "position_dzi_px": [read_x_rel, read_y_rel],
                        "bounds_offset": [bounds_x, bounds_y],
                    }
                    if mpp_x > 0 and mpp_y > 0:
                        effective_mpp_x = mpp_x * downsample
                        effective_mpp_y = mpp_y * downsample
                        tile_meta["mpp_x"] = effective_mpp_x
                        tile_meta["mpp_y"] = effective_mpp_y
                        tile_meta["position_um"] = [read_x * mpp_x, read_y * mpp_y]
                        tile_meta["tile_size_um"] = [tw * effective_mpp_x, th * effective_mpp_y]

                    if use_blosc:
                        arr = np.array(region, dtype=np.uint8)
                        compressed = blosc2.compress(arr.tobytes(), typesize=1, clevel=5, filter=blosc2.Filter.SHUFFLE)
                        ext = "blosc"
                        # Store shape info in meta
                        tile_meta["array_shape"] = list(arr.shape)
                        tile_meta["array_dtype"] = "uint8"
                        zf.writestr(f"{ann_folder}/{tile_name}.{ext}", compressed)
                    else:
                        buf = io.BytesIO()
                        region.save(buf, format="JPEG", quality=90)
                        zf.writestr(f"{ann_folder}/{tile_name}.jpeg", buf.getvalue())

                    manifest_entries.append(tile_meta)
                    tile_idx += 1

        # Write manifest
        manifest = {
            "slide_name": Path(slide_path).name,
            "slide_path": slide_path,
            "slide_dimensions": list(slide.dimensions),
            "level_count": slide.level_count,
            "level_dimensions": [list(d) for d in slide.level_dimensions],
            "level_downsamples": list(slide.level_downsamples),
            "mpp_x": mpp_x,
            "mpp_y": mpp_y,
            "export_params": {
                "target_level": target_level,
                "tile_size": tile_size,
                "format": fmt,
                "downsample": downsample,
            },
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total_tiles": len(manifest_entries),
            "tiles": manifest_entries,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    tmp.seek(0)
    return send_file(
        tmp,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{slide_stem}_tiles_L{target_level}_{tile_size}px.zip",
    )


# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FoetoPath MRXS Slide Viewer")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--root", default="", help="Default root folder for slides")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    app.config["DEFAULT_ROOT"] = args.root

    print(f"\n{'='*60}")
    print(f"  FoetoPath MRXS Slide Viewer")
    print(f"  http://{args.host}:{args.port}")
    if args.root:
        print(f"  Root: {args.root}")
    print(f"{'='*60}\n")

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
