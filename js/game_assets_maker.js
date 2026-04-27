import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SPINE_EXPORT_NODE = "GameAssets_RigToSpineExport";
const ASEPRITE_ATLAS_NODE = "GameAssets_AsepriteVisualNovelAtlas";
const ASEPRITE_ANIMATION_NODE = "GameAssets_AsepriteAnimationTags";
const ASEPRITE_ANIMATION_ATLAS_NODE = "GameAssets_AsepriteAnimationAtlas";
const ASEPRITE_ANIMATION_DIRECTIONS = ["forward", "reverse", "pingpong"];

function filenameFromDisposition(disposition, fallback) {
    const match = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(disposition || "");
    const value = match?.[1] || match?.[2];
    return value ? decodeURIComponent(value) : fallback;
}

function rememberExport(node, output) {
    const payload = output?.game_assets_spine?.[0] || output?.game_assets_spine;
    if (payload?.export_dir) {
        node.gameAssetsMakerSpineExport = payload;
    }
}

async function downloadLatestExport(node) {
    const exportInfo = node.gameAssetsMakerSpineExport;
    if (!exportInfo?.export_dir) {
        app.extensionManager?.toast?.add?.({
            severity: "warn",
            summary: "Spine export",
            detail: "Execute the node before downloading.",
            life: 3000,
        });
        return;
    }

    const response = await api.fetchApi(
        `/game_assets_maker/download_spine?dir=${encodeURIComponent(exportInfo.export_dir)}`
    );
    if (!response.ok) {
        let message = `Download failed (${response.status})`;
        try {
            const data = await response.json();
            message = data.error || message;
        } catch (_error) {
            // Keep the status-based message when the response is not JSON.
        }
        throw new Error(message);
    }

    const blob = await response.blob();
    const filename = filenameFromDisposition(
        response.headers.get("content-disposition"),
        `${exportInfo.asset_name || "spine_export"}.zip`
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function addDownloadButton(node) {
    if (node.gameAssetsMakerDownloadButton) {
        return;
    }
    node.gameAssetsMakerDownloadButton = node.addWidget("button", "Download Spine ZIP", null, async () => {
        try {
            await downloadLatestExport(node);
        } catch (error) {
            app.extensionManager?.toast?.add?.({
                severity: "error",
                summary: "Spine export",
                detail: error?.message || "Could not download Spine export.",
                life: 5000,
            });
            console.error("[GameAssetsMaker] Spine ZIP download failed", error);
        }
    });
}

function findWidget(node, name) {
    return node.widgets?.find((widget) => widget.name === name);
}

function spriteNameWidgets(node) {
    return (node.widgets || []).filter((widget) => widget.name?.startsWith("sprite_") && widget.name?.endsWith("_name"));
}

function parseSpriteNames(value, count) {
    let names = [];
    const text = String(value || "").trim();
    if (text) {
        try {
            const parsed = JSON.parse(text);
            if (Array.isArray(parsed)) {
                names = parsed.map((item) => String(item || ""));
            }
        } catch (_error) {
            names = text.split(/\r?\n/);
        }
    }
    while (names.length < count) {
        names.push(`sprite_${String(names.length + 1).padStart(2, "0")}`);
    }
    return names.slice(0, count);
}

function spriteCount(node) {
    const countWidget = findWidget(node, "sprite_count");
    const count = Number.parseInt(countWidget?.value, 10);
    return Number.isFinite(count) ? Math.max(1, Math.min(128, count)) : 1;
}

function animationCount(node) {
    const countWidget = findWidget(node, "animation_count");
    const count = Number.parseInt(countWidget?.value, 10);
    return Number.isFinite(count) ? Math.max(1, Math.min(128, count)) : 1;
}

function syncSpriteNamesToHiddenWidget(node) {
    const hiddenWidget = findWidget(node, "sprite_names");
    if (!hiddenWidget) {
        return;
    }
    const names = spriteNameWidgets(node)
        .sort((a, b) => Number(a.gameAssetsMakerSpriteIndex) - Number(b.gameAssetsMakerSpriteIndex))
        .map((widget, index) => String(widget.value || `sprite_${String(index + 1).padStart(2, "0")}`).trim());
    hiddenWidget.value = JSON.stringify(names);
}

function animationWidgets(node) {
    return (node.widgets || []).filter((widget) => widget.name?.startsWith("animation_") && widget.gameAssetsMakerAnimationField);
}

function parseAnimationTags(value, count, frameCount) {
    let tags = [];
    const text = String(value || "").trim();
    if (text) {
        try {
            const parsed = JSON.parse(text);
            if (Array.isArray(parsed)) {
                tags = parsed;
            }
        } catch (_error) {
            tags = [];
        }
    }
    while (tags.length < count) {
        tags.push({
            name: `animation_${String(tags.length + 1).padStart(2, "0")}`,
            from: 0,
            to: Math.max(0, frameCount - 1),
            direction: "forward",
            color: "#000000ff",
        });
    }
    return tags.slice(0, count);
}

function syncAnimationTagsToHiddenWidget(node) {
    const hiddenWidget = findWidget(node, "animation_tags");
    if (!hiddenWidget) {
        return;
    }
    const grouped = new Map();
    for (const widget of animationWidgets(node)) {
        const index = Number(widget.gameAssetsMakerAnimationIndex);
        if (!grouped.has(index)) {
            grouped.set(index, {});
        }
        grouped.get(index)[widget.gameAssetsMakerAnimationField] = widget.value;
    }
    const tags = [...grouped.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([index, tag]) => ({
            name: String(tag.name || `animation_${String(index + 1).padStart(2, "0")}`).trim(),
            from: Number.parseInt(tag.from, 10) || 0,
            to: Number.parseInt(tag.to, 10) || 0,
            direction: ASEPRITE_ANIMATION_DIRECTIONS.includes(String(tag.direction)) ? String(tag.direction) : "forward",
            color: String(tag.color || "#000000ff").trim(),
        }));
    hiddenWidget.value = JSON.stringify(tags);
}

function hideRawSpriteNamesWidget(node) {
    const widget = findWidget(node, "sprite_names");
    if (!widget || widget.gameAssetsMakerHidden) {
        return;
    }
    const originalSerializeValue = widget.serializeValue?.bind(widget);
    widget.gameAssetsMakerHidden = true;
    widget.hidden = true;
    widget.computeSize = () => [0, -4];
    widget.serializeValue = () => {
        syncSpriteNamesToHiddenWidget(node);
        return originalSerializeValue ? originalSerializeValue() : widget.value;
    };
    for (const element of [widget.element, widget.inputEl, widget.textElement]) {
        if (element?.style) {
            element.style.display = "none";
            element.style.height = "0";
            element.style.minHeight = "0";
            element.style.overflow = "hidden";
        }
    }
}

function hideRawAnimationTagsWidget(node) {
    const widget = findWidget(node, "animation_tags");
    if (!widget || widget.gameAssetsMakerHidden) {
        return;
    }
    const originalSerializeValue = widget.serializeValue?.bind(widget);
    widget.gameAssetsMakerHidden = true;
    widget.hidden = true;
    widget.computeSize = () => [0, -4];
    widget.serializeValue = () => {
        syncAnimationTagsToHiddenWidget(node);
        return originalSerializeValue ? originalSerializeValue() : widget.value;
    };
}

function setWidgetVisible(node, widget, visible) {
    if (!widget) {
        return;
    }
    if (visible) {
        widget.hidden = false;
        if (widget.gameAssetsMakerOriginalComputeSize) {
            widget.computeSize = widget.gameAssetsMakerOriginalComputeSize;
        }
    } else {
        if (!widget.gameAssetsMakerOriginalComputeSize) {
            widget.gameAssetsMakerOriginalComputeSize = widget.computeSize;
        }
        widget.hidden = true;
        widget.computeSize = () => [0, -4];
    }
    node.setSize?.(node.computeSize?.());
    app.graph?.setDirtyCanvas?.(true, true);
}

function updateLayoutWidgets(node) {
    const layoutWidget = findWidget(node, "layout_direction");
    const columnsWidget = findWidget(node, "columns");
    const showColumns = String(layoutWidget?.value || "Horizontal") === "Grid";
    setWidgetVisible(node, columnsWidget, showColumns);
}

function removeSpriteNameWidgets(node) {
    for (const widget of spriteNameWidgets(node)) {
        if (typeof node.removeWidget === "function") {
            node.removeWidget(widget);
        } else {
            const index = node.widgets.indexOf(widget);
            if (index >= 0) {
                node.widgets.splice(index, 1);
            }
        }
    }
}

function removeAnimationWidgets(node) {
    for (const widget of animationWidgets(node)) {
        if (typeof node.removeWidget === "function") {
            node.removeWidget(widget);
        } else {
            const index = node.widgets.indexOf(widget);
            if (index >= 0) {
                node.widgets.splice(index, 1);
            }
        }
    }
}

function rebuildSpriteNameWidgets(node) {
    const count = spriteCount(node);
    const hiddenWidget = findWidget(node, "sprite_names");
    const existingNames = parseSpriteNames(hiddenWidget?.value, count);

    removeSpriteNameWidgets(node);
    for (let index = 0; index < count; index += 1) {
        const widget = node.addWidget("text", `sprite_${index + 1}_name`, existingNames[index], () => {
            syncSpriteNamesToHiddenWidget(node);
        });
        widget.gameAssetsMakerSpriteIndex = index;
        widget.beforeQueued = () => syncSpriteNamesToHiddenWidget(node);
    }

    syncSpriteNamesToHiddenWidget(node);
    node.setSize?.(node.computeSize?.());
    app.graph?.setDirtyCanvas?.(true, true);
}

function addAnimationWidget(node, type, name, value, callback, options) {
    const widget = node.addWidget(type, name, value, callback, options);
    widget.beforeQueued = () => syncAnimationTagsToHiddenWidget(node);
    return widget;
}

function rebuildAnimationWidgets(node) {
    const count = animationCount(node);
    const frameCountWidget = findWidget(node, "frame_count");
    const frameCount = Math.max(1, Number.parseInt(frameCountWidget?.value, 10) || 1);
    const hiddenWidget = findWidget(node, "animation_tags");
    const tags = parseAnimationTags(hiddenWidget?.value, count, frameCount);

    removeAnimationWidgets(node);
    for (let index = 0; index < count; index += 1) {
        const tag = tags[index] || {};
        const fields = [
            addAnimationWidget(node, "text", `animation_${index + 1}_name`, tag.name, () => syncAnimationTagsToHiddenWidget(node)),
            addAnimationWidget(node, "number", `animation_${index + 1}_from`, tag.from, () => syncAnimationTagsToHiddenWidget(node), {
                min: 0,
                max: Math.max(0, frameCount - 1),
                step: 1,
            }),
            addAnimationWidget(node, "number", `animation_${index + 1}_to`, tag.to, () => syncAnimationTagsToHiddenWidget(node), {
                min: 0,
                max: Math.max(0, frameCount - 1),
                step: 1,
            }),
            addAnimationWidget(node, "combo", `animation_${index + 1}_direction`, tag.direction, () => syncAnimationTagsToHiddenWidget(node), {
                values: ASEPRITE_ANIMATION_DIRECTIONS,
            }),
            addAnimationWidget(node, "text", `animation_${index + 1}_color`, tag.color, () => syncAnimationTagsToHiddenWidget(node)),
        ];
        for (const widget of fields) {
            widget.gameAssetsMakerAnimationIndex = index;
            widget.gameAssetsMakerAnimationField = widget.name.split("_").slice(2).join("_");
        }
    }

    syncAnimationTagsToHiddenWidget(node);
    node.setSize?.(node.computeSize?.());
    app.graph?.setDirtyCanvas?.(true, true);
}

function setupAsepriteAtlasNode(node) {
    if (node.gameAssetsMakerAsepriteSetup) {
        updateLayoutWidgets(node);
        rebuildSpriteNameWidgets(node);
        return;
    }
    node.gameAssetsMakerAsepriteSetup = true;
    hideRawSpriteNamesWidget(node);
    updateLayoutWidgets(node);

    const countWidget = findWidget(node, "sprite_count");
    if (countWidget && !countWidget.gameAssetsMakerWrapped) {
        const originalCallback = countWidget.callback;
        countWidget.callback = function () {
            originalCallback?.apply(this, arguments);
            rebuildSpriteNameWidgets(node);
        };
        countWidget.gameAssetsMakerWrapped = true;
    }

    const layoutWidget = findWidget(node, "layout_direction");
    if (layoutWidget && !layoutWidget.gameAssetsMakerWrapped) {
        const originalCallback = layoutWidget.callback;
        layoutWidget.callback = function () {
            originalCallback?.apply(this, arguments);
            updateLayoutWidgets(node);
        };
        layoutWidget.gameAssetsMakerWrapped = true;
    }

    rebuildSpriteNameWidgets(node);
}

function setupAsepriteAnimationNode(node) {
    if (node.gameAssetsMakerAnimationSetup) {
        updateLayoutWidgets(node);
        rebuildAnimationWidgets(node);
        return;
    }
    node.gameAssetsMakerAnimationSetup = true;
    hideRawAnimationTagsWidget(node);
    updateLayoutWidgets(node);

    for (const widgetName of ["animation_count", "frame_count", "layout_direction"]) {
        const widget = findWidget(node, widgetName);
        if (widget && !widget.gameAssetsMakerWrapped) {
            const originalCallback = widget.callback;
            widget.callback = function () {
                originalCallback?.apply(this, arguments);
                updateLayoutWidgets(node);
                rebuildAnimationWidgets(node);
            };
            widget.gameAssetsMakerWrapped = true;
        }
    }

    rebuildAnimationWidgets(node);
}

app.registerExtension({
    name: "ComfyUI.GameAssetsMaker",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === SPINE_EXPORT_NODE) {
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                originalOnNodeCreated?.apply(this, arguments);
                addDownloadButton(this);
            };

            const originalOnExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (output) {
                originalOnExecuted?.apply(this, arguments);
                rememberExport(this, output);
            };
        }

        if (nodeData.name === ASEPRITE_ATLAS_NODE) {
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                originalOnNodeCreated?.apply(this, arguments);
                setupAsepriteAtlasNode(this);
            };

            const originalOnSerialize = nodeType.prototype.onSerialize;
            nodeType.prototype.onSerialize = function () {
                syncSpriteNamesToHiddenWidget(this);
                return originalOnSerialize?.apply(this, arguments);
            };
        }

        if (nodeData.name === ASEPRITE_ANIMATION_NODE) {
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                originalOnNodeCreated?.apply(this, arguments);
                setupAsepriteAnimationNode(this);
            };

            const originalOnSerialize = nodeType.prototype.onSerialize;
            nodeType.prototype.onSerialize = function () {
                syncAnimationTagsToHiddenWidget(this);
                return originalOnSerialize?.apply(this, arguments);
            };
        }

        if (nodeData.name === ASEPRITE_ANIMATION_ATLAS_NODE) {
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                originalOnNodeCreated?.apply(this, arguments);
                setupAsepriteAnimationNode(this);
            };

            const originalOnSerialize = nodeType.prototype.onSerialize;
            nodeType.prototype.onSerialize = function () {
                syncAnimationTagsToHiddenWidget(this);
                return originalOnSerialize?.apply(this, arguments);
            };
        }
    },

    loadedGraphNode(node) {
        if (node.comfyClass === SPINE_EXPORT_NODE) {
            addDownloadButton(node);
        }
        if (node.comfyClass === ASEPRITE_ATLAS_NODE) {
            setupAsepriteAtlasNode(node);
        }
        if (node.comfyClass === ASEPRITE_ANIMATION_NODE) {
            setupAsepriteAnimationNode(node);
        }
        if (node.comfyClass === ASEPRITE_ANIMATION_ATLAS_NODE) {
            setupAsepriteAnimationNode(node);
        }
    },
});
