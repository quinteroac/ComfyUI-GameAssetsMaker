# ComfyUI Game Assets Maker

Custom ComfyUI nodes for game asset preparation workflows.

## Installation

1. Navigate to your ComfyUI `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
```

2. Clone this repository:

```bash
git clone https://github.com/YOUR_USERNAME/ComfyUI-GameAssetsMaker.git
```

3. Install dependencies:

```bash
cd ComfyUI-GameAssetsMaker
pip install -r requirements.txt
```

4. Restart ComfyUI.

## Nodes

### Aseprite Visual Novel Atlas

Generates an Aseprite-compatible JSON atlas for visual novel character spritesheets.

Set the spritesheet width/height, choose **Half Body**, **Full Body**, or **Face Expressions**, then set the sprite count. The frontend adds one name field per sprite and stores those names in the atlas frame keys, such as `neutral.png`, `happy.png`, or `angry.png`.

Use `layout_direction` to choose **Horizontal**, **Vertical**, or **Grid**. Horizontal places all sprites in one row, Vertical places all sprites in one column, and Grid balances the layout automatically when `columns` is `0` (`4 -> 2x2`, `6 -> 3x2`, `9 -> 3x3`). Set `columns` above `0` to force a manual grid. The node validates that the sheet divides evenly into uniform frames. The output JSON uses Aseprite's hash format with `frames` coordinates and `meta.image` pointing at `image_filename`.

### Aseprite Atlas Sprite Preview

Receives an Aseprite JSON atlas and the spritesheet image, then crops every atlas frame into a separate image in a ComfyUI `IMAGE` batch.

Connect `aseprite_json` from **Aseprite Visual Novel Atlas** and connect the generated/loaded spritesheet image to `spritesheet`. The `sprites` output can go into **Preview Image** or **Save Image** to inspect each sprite separately. If incoming atlas frames have different sizes, the node pads them onto the largest frame canvas so they can stay in one batch.

### Aseprite Animation Tags

Generates Aseprite-style animation metadata using `meta.frameTags`.

Set the total `frame_count` and `animation_count`; the frontend adds fields for each animation tag: name, first frame, last frame, playback direction, and tag color. Directions use Aseprite's exported values: `forward`, `reverse`, or `pingpong`.

Optionally connect an existing atlas JSON to `aseprite_json`. In that mode the node returns a merged JSON containing the original atlas frames plus `meta.frameTags`, ready for engines or importers that read animations from Aseprite frame tags.

### Aseprite Animation Atlas

Generates a standalone Aseprite-compatible JSON atlas for animation spritesheets.

This node is separate from **Aseprite Visual Novel Atlas**. Use it when the spritesheet contains animation frames such as `idle_01`, `idle_02`, `idle_03`, and `idle_04`, not character expression states like `neutral`, `happy`, `sad`, or `angry`.

Set the spritesheet size, total `frame_count`, layout direction, and animation tags. For example, an `idle` animation with 4 frames uses a single tag named `idle` from frame `0` to `3`. The node creates both `frames` and `meta.frameTags` in one JSON.

### Aseprite Animation Preview

Receives a spritesheet image and an Aseprite JSON atlas with `meta.frameTags`, then extracts the selected animation as an `IMAGE` batch.

Set `animation_name` to one of the frame tag names. The node resolves `forward`, `reverse`, and `pingpong` playback order, repeats it with `loop_count`, and outputs `frame_delay_ms` from the atlas frame durations. Connect `animation_frames` to **Preview Image** or to an animated WebP/GIF/video saver node.

### Aseprite VLM Audit Prompt

Builds a strict JSON-only prompt for a vision-language model to inspect a generated spritesheet and correct Aseprite frame rectangles.

Connect the generated spritesheet image and either a visual novel atlas JSON or an animation atlas JSON. Send the output image plus `vlm_prompt` into any VLM node that accepts image + text and returns text.

### Aseprite Apply VLM Correction

Applies the VLM's JSON response to the original Aseprite atlas.

The VLM response can be either a list of frame rectangles or a full `frames` object. The node clamps rectangles to the actual image size, preserves existing frame names when possible, updates `meta.size`, and returns a corrected Aseprite JSON. This works for both visual novel atlases and animation atlases.

### SeeThrough Parts To SVG Paths

Converts `SEETHROUGH_PARTS` from `SeeThrough_PostProcess` into SVG path outlines.

The node traces each part's alpha mask, optionally preserves internal holes with `hierarchy` mode, orders parts by `depth_median`, and can save the generated SVG to ComfyUI's output directory.

The first output is a ComfyUI `SVG`-compatible object and can be connected directly to the built-in **Save SVG** node. The second output is the same SVG as text for inspection, and the third is the path written by this node when `save_svg` is enabled.

It intentionally ignores source colors and textures. The output is vector tracing of layer silhouettes only.

### SeeThrough Parts Rig Probe

Tests whether `SEETHROUGH_PARTS` tags are useful enough to seed a 2D rig.

The node reads each part tag, estimates semantic role and side, calculates alpha-based bounds, pivots, and a simple bone hierarchy, then returns `GAME_ASSET_RIG`, editable JSON, and a text report.

Use the report output to inspect whether tags like `left_arm`, `head`, `torso`, `right_hand`, or similar names are being classified correctly before building a full interactive rig editor.

### Apply Rig Overrides

Applies manual JSON edits to a `GAME_ASSET_RIG` while preserving the generated structure.

Override parts by `id`, `tag`, or `tag:<exact tag>`, and override bones by bone `id`:

```json
{
	"parts": {
		"face": { "pivot": [653, 650] },
		"tag:front hair": { "pivot": [704, 526] }
	},
	"bones": {
		"face_bone": { "parent": "neck_bone" }
	}
}
```

The node returns the adjusted `GAME_ASSET_RIG`, updated JSON, and a report of what changed.

### SeeThrough Parts Pose Rig

Builds a `GAME_ASSET_RIG` from `SEETHROUGH_PARTS` plus `POSE_KEYPOINT` from **DWPose Estimator** or **OpenPose Pose** in `comfyui_controlnet_aux`.

Recommended wiring:

```text
image -> DWPose Estimator.pose_kps
SeeThrough_PostProcess.parts -> SeeThrough Parts Pose Rig.parts
DWPose Estimator.pose_kps -> SeeThrough Parts Pose Rig.pose_kps
SeeThrough Parts Pose Rig.rig -> Rig To SVG Preview.rig
```

The node first creates the same automatic tag/bounds rig as **SeeThrough Parts Rig Probe**, then replaces pivots with pose keypoints when available. Missing keypoints fall back to the automatic pivot.

Current pose mapping:

```text
face/head -> neck
neck -> neck
nose -> nose
mouth -> mouth landmarks
eye left/right -> left_eye/right_eye
upper_arm -> shoulder
forearm -> elbow
hand -> wrist
thigh -> hip
calf -> knee
foot -> ankle
```

The `report` output lists how many pivots came from pose data and which parts used fallback.

### Rig To SVG Preview

Renders a `GAME_ASSET_RIG` as an SVG overlay with bounds, bones, pivots, and optional labels. The first output can connect directly to ComfyUI's built-in **Save SVG** node.

### Rig To Spine Export

Exports `SEETHROUGH_PARTS` plus a `GAME_ASSET_RIG` as a Spine-style region attachment package.

Recommended wiring:

```text
SeeThrough_PostProcess.parts -> Rig To Spine Export.parts
SeeThrough Parts Pose Rig.rig -> Rig To Spine Export.rig
```

The node writes a timestamped folder under ComfyUI's output directory:

```text
game_asset_spine_YYYYmmdd_HHMMSS_xxxxxxxx/
	game_asset_spine_YYYYmmdd_HHMMSS_xxxxxxxx.json
	game_asset_spine_YYYYmmdd_HHMMSS_xxxxxxxx.atlas
	images/
		face.png
		front-hair.png
		topwear.png
```

Each SeeThrough part becomes a PNG region attachment. Rig bones become Spine bones, slots preserve the rig part order, and image-space Y coordinates are converted to Spine's Y-up coordinates. Exported bones include `length` and setup `rotation` derived from the rig head/tail points, while attachments are counter-rotated in local space so the assembled character stays aligned on import.

Common semantic parts get a minimal control hierarchy before export. Body/topwear and face become structural controls; hair, iris, and eyelashes become point controls for sway, eye movement, and blinking. Static details like legwear, footwear, ears, brows, nose, mouth, neck, and handwear remain as attachments mapped to the nearest useful control instead of creating extra bones. Attachment paths use the region name, such as `face`, while atlas pages point to files such as `images/face.png`.

After the node runs, use the **Download Spine ZIP** button on the node to download the generated JSON, atlas, and image folder as a single archive.

Automatic animation controls:

```text
animation_preset: all / full_idle / idle_breath / blink / hair_sway / face_alive / no_animations
animation_duration: loop length in seconds
animation_intensity: transform strength multiplier
blink_interval: seconds between blinks
```

The default `all` preset writes separate `idle_breath`, `blink`, `hair_sway`, and `face_alive` animations, plus a combined loop named `idle`. These animations use bone transforms only, so they work with the region attachments generated by this MVP exporter.

This is an MVP region-attachment export, not a mesh/skinning export yet. It is intended as a starting point for importing and refining in Spine.
